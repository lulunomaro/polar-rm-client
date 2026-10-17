# Video cleanup recovery at the window boundary

The ordinary HUD displays a small recovery status only while the backend owns a
pending or failed video cleanup. It is drawn by the live interaction layer,
never the cached video background. It remains visible if the operator selected
Synthetic or Webcam while an old native decoder still owns a failed handle.
Healthy rendering, settings catalog and persisted source preference are unchanged.

The backend snapshot distinguishes actual in-flight work from ended-but-retained
native owners. A running closer shows a waiting status. Once no closer is running,
retained ownership or an admission failure exposes an explicit retry button.
Current close failures and admission errors are shown; historical `last_error`
strings are not used as evidence. The full details remain in the button tooltip.

Each pointer intent carries both the video request generation and the whole
backend lifecycle generation. The adapter rejects stale A→B→A clicks, hidden or
modal surfaces, pending/failed runtime transitions, incomplete profile changes,
and final close. The backend independently checks the source generation. The
click/release pair is consumed by UI ownership and cannot become combat input.
Retry admission is not reported as successful resource release. The visible
waiting button also consumes its left press/release (including release outside
the button), without retrying or publishing combat input. Its shield is removed
when the status is no longer drawn; healthy HUD geometry is unchanged.

Periodic refresh never calls the retry API. Its retained errors enter the existing
typed/throttled tick diagnostic boundary, allowing later backend/safety stages and
frame requests to continue. A failed explicit retry has a traceback and visible
warning, and preserves the backend's retained failure state.

## Evidence boundaries

- Pure projection tests cover visibility, in-flight versus retained ownership,
  failure details, and both generations.
- Actual-window tests exercise the real manager, cleanup workers, production
  mouse dispatch and QPainter drawing. Only native process/pipe terminals are
  inert, and the final offscreen window painter targets a real QImage.
- A Python socket audit rejects network access. Physical serial enumeration is
  replaced with an empty result; the independent discovery cache/worker remains
  real and is not mistaken for a video cleanup retry.
- Native Windows/OpenGL presentation, actual cameras and firmware remain outside
  this evidence. Local inference/Tongji runtime stop admission is a separate work
  item and is not implied by this backend video recovery control.
