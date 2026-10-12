"""Pure observer readiness predicate. It deliberately does not require an image."""
def observer_ready(snapshot):
 s=snapshot
 return bool(s.get('shown') is True and s.get('role')=='spectator'
  and type(s.get('session_id')) is str and 0<len(s['session_id'])<=128
  and s.get('uncertain') is False and s.get('watch_robot')=='red_sentry'
  and type(s.get('watch_epoch')) is int and s['watch_epoch']>=0
  and type(s.get('episode')) is int and s['episode']>=0
  and s.get('in_registry') is True and s.get('selection_pending') is False
  and s.get('owned_robot') is None and s.get('claim_pending') is False and s.get('armed') is False)
