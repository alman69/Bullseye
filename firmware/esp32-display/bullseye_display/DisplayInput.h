#pragma once
#include "DisplayProtocol.h"
#include "TouchInput.h"

namespace bullseye {
inline void handleGesture(Model& model, const onebox::Gesture& gesture, uint32_t) {
  if (gesture.kind == onebox::GestureKind::Left) model.page = (model.page + 1) % kPageCount;
  else if (gesture.kind == onebox::GestureKind::Right) model.page = (model.page + kPageCount - 1) % kPageCount;
  // Navigation is swipe-only. Former tab/button hit regions have no effect.
}
}  // namespace bullseye
