"""V2-only routed chassis yaw; original v1 and physical egress stay untouched."""
import time
from .sim_operator_runtime import SimInputBridge

class PlatformInputBridge(SimInputBridge):
    def build_command(self,state,registry):
        # Original command construction converts numeric settings before the
        # v2 send hook. Corrupt settings must revoke on keys/releases too, not
        # wait for a later UI heartbeat or leak an exception from that slot.
        try:
            modes=self.runtime.modes
            if getattr(modes,'window',None) is not None:modes.sync(modes.window)
            return super().build_command(state,registry)
        except (ValueError,TypeError,OverflowError):
            from .control import ChassisCommand
            self.runtime.disarm('Invalid v2 input configuration; correct settings before F2')
            self.reset_inputs();self._consume_relative_motion();self.deferred_pending=False
            return ChassisCommand(seq=self.seq,timestamp=time.time(),vx=0.,vy=0.,wz=0.,
                spin_mode=False,speed_limit=0,power_limit=0,mock_output=True)
    def send_command(self,command):
        # Never call physical KeyboardControlBridge.send_command or v1 rate paths.
        self.last_command=command;self.last_send_at=time.perf_counter()
        if getattr(self,'defer_sim_command',False):
            self.deferred_pending=True;return
        try:spin=self.runtime.modes.request() if hasattr(self.runtime.modes,'request') else None
        except (ValueError,TypeError,KeyError):
            self.runtime.disarm('Invalid v2 spin configuration')
            self.reset_inputs();self._consume_relative_motion();return
        if command.spin_mode and spin is None:
            self.runtime.disarm('V2 spin unavailable: release inputs and rearm')
            self.reset_inputs();self._consume_relative_motion();return
        try:
            self.runtime.transport.submit_input(vx_mps=command.vx,vy_mps=-command.vy,
                chassis_yaw_rps=command.wz,spin_request=spin if command.spin_mode else None,fire=bool(command.left_button_down))
        except (ValueError,TypeError):
            self.runtime.disarm('Invalid v2 virtual input: release and rearm')
            self.reset_inputs();self._consume_relative_motion();return
        self.runtime.transport.submit_mouse(-command.mouse_x*.002,command.mouse_y*.002)
        self.runtime.modes.submit(command.right_button_down)
        self._dirty=False;self._consume_relative_motion()
