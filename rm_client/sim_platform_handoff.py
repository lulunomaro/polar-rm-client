"""Bounded local scheduling estimates. Server deadlines remain authoritative.

All local bounds are anchored at request *start*, never reply receipt. Only a
relative delta within one verified server window crosses the clock boundary.
No networking, workers, retry queue or authority creation lives in this module.
"""
import math


def number(v):
    if type(v) not in (float, int):
        raise ValueError('Invalid handoff time')
    try:
        v = float(v)
    except (OverflowError, ValueError):
        raise ValueError('Invalid handoff time') from None
    if not math.isfinite(v):
        raise ValueError('Invalid handoff time')
    return v


class HandoffSchedule:
    PERIOD = .020
    ISSUE_PERIOD = .050
    SAMPLE_BUDGET = .020
    WAKE_BUDGET = .005
    MARGIN = .005

    def __init__(self):
        self.costs = {k: .010 for k in ('window', 'pulse', 'control', 'renew', 'keepalive')}
        self.reset()

    def reset(self):
        """New control generation; observed session RTT budgets deliberately persist."""
        self.window = None
        self.started = self.deadline = None
        self.phase = 'NO_WINDOW'
        self.last_issue = self.last_batch = -math.inf
        self.activation = {}
        self.handoff_reason = 'no_window'
        self.population_attempted = set()
        self.population_acked = set()
        self.initial_batch = False

    def new_session(self):
        self.__init__()

    def observe(self, kind, start, end):
        elapsed = number(end) - number(start)
        if kind not in self.costs or elapsed < 0:
            raise ValueError('Invalid handoff measurement')
        self.costs[kind] = max(self.costs[kind], elapsed)

    def install(self, window, started, now):
        if type(window) is not dict or type(window.get('window_id')) is not int:
            raise ValueError('Invalid handoff window')
        ttl = number(window.get('valid_for_s'))
        issued = number(window.get('issued_wall'))
        expires = number(window.get('expires_wall'))
        if not 0 < ttl <= .150 or not issued < expires or abs(expires-issued-ttl) > 1e-6:
            raise ValueError('Invalid handoff lifetime')
        started, now = number(started), number(now)
        if now < started or now >= started + ttl:
            raise ValueError('Late handoff window')
        self.window = dict(window)
        self.started, self.deadline = started, started + ttl
        self.last_issue = started
        self.phase = 'WAIT_POST_ACK_SAMPLE'
        self.population_attempted.clear();self.population_acked.clear()

    def acknowledge(self, kind, ack):
        """Caller checks full SDK identity first, then installs this kind only."""
        if (self.window is None or type(ack.get('window_id')) is not int
                or ack['window_id'] != self.window['window_id']):
            raise ValueError('Wrong activation window')
        expires = number(ack.get('activation_expires'))
        delta = expires - self.window['issued_wall']
        if (not self.window['issued_wall'] < expires <= self.window['expires_wall']
                or not 0 < delta <= self.window['valid_for_s'] + 1e-6):
            raise ValueError('Invalid activation delta')
        self.activation[kind] = min(self.started + delta, self.deadline)
        if self.initial_batch:self.population_acked.add(kind)

    def expired(self, now):
        now = number(now)
        if self.deadline is not None and now >= self.deadline:
            return 'window_expired'
        if any(now >= expiry for expiry in self.activation.values()):
            return 'activation_expired'
        return None

    def cost(self, kinds, controls, maintenance=()):
        return (self.costs['window'] + self.SAMPLE_BUDGET + self.WAKE_BUDGET + self.MARGIN
                + kinds*self.costs['pulse'] + controls*self.costs['control']
                + sum(self.costs[k] for k in maintenance))

    def next_action(self, now, *, kinds, controls, fresh, maintenance=()):
        """Returns fetch/wait/send or a terminal local stop reason."""
        now = number(now)
        expired = self.expired(now)
        if expired:
            return expired
        cost = self.cost(kinds, controls, maintenance)
        ttl = .150 if self.window is None else self.window['valid_for_s']
        if cost >= ttl:
            return 'budget_pressure'
        if self.window is None:
            return 'fetch'
        if self.phase == 'WAIT_POST_ACK_SAMPLE':
            return 'send' if fresh else 'wait'
        if self.phase in ('FETCHING','POPULATING'):return 'wait'
        earliest = min([self.deadline, *self.activation.values()])
        trigger = earliest - (max(.075, cost) + self.WAKE_BUDGET + self.MARGIN)
        batch_cost = kinds*self.costs['pulse'] + controls*self.costs['control']
        crosses_trigger = fresh and now + batch_cost + self.WAKE_BUDGET >= trigger
        if now >= trigger or crosses_trigger:
            earliest_issue = max(now, self.last_issue + self.ISSUE_PERIOD)
            if earliest_issue >= earliest:
                self.handoff_reason = 'issue_interval_reaches_deadline'
                return 'budget_pressure'
            if now < earliest_issue:
                self.handoff_reason = 'waiting_issue_interval'
                return 'wait'  # No new old-window batch while handoff is pending.
            self.handoff_reason = ('forecast_insufficient_one_attempt' if now + cost >= earliest
                                   else 'batch_crosses_prefetch' if crosses_trigger and now < trigger
                                   else 'prefetch_due_budget_fits')
            return 'fetch'
        if fresh and now - self.last_batch >= self.PERIOD - 1e-12:
            return 'send'
        return 'wait'

    def begin_batch(self, now):
        self.initial_batch = self.phase == 'WAIT_POST_ACK_SAMPLE'
        self.last_batch = number(now)
        self.phase = 'POPULATING'

    def mark_kind_attempt(self, kind):
        if self.initial_batch:
            if kind in self.population_attempted:
                raise ValueError('Initial handoff kind already attempted')
            self.population_attempted.add(kind)

    def finish_batch(self):
        self.phase = 'CURRENT'
