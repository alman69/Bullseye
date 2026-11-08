#pragma once
#include <math.h>
#include <string.h>
#include <cJSON.h>
#include "DisplayProtocol.h"

namespace bullseye {
inline bool jsonUint(const cJSON* item, uint32_t& value, uint32_t min = 0) {
  if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) || item->valuedouble < min ||
      item->valuedouble > UINT32_MAX || floor(item->valuedouble) != item->valuedouble) return false;
  value = static_cast<uint32_t>(item->valuedouble); return true;
}
inline bool decodeSample(const Frame& frame, Sample& sample) {
  if (!frame.length || frame.length >= kMaxFrame || frame.text[frame.length] != '\0' ||
      memchr(frame.text, '\0', frame.length) || strstr(frame.text, "\\u0000")) return false;
  const char* end = nullptr;
  cJSON* root = cJSON_ParseWithLengthOpts(frame.text, frame.length + 1, &end, true);
  if (!root) return false;
  bool valid = cJSON_IsObject(root) && end == frame.text + frame.length;
  constexpr const char* names[] = {"v", "seq", "distance_cm", "status", "sample_age_ms", "rover_connected", "source"};
  unsigned fields = 0;
  for (const cJSON* child = root->child; child && valid; child = child->next) {
    unsigned index = 0;
    for (; index < 7 && strcmp(child->string ? child->string : "", names[index]); ++index) {}
    if (index == 7 || fields & (1u << index)) valid = false;
    else fields |= 1u << index;
  }
  valid = valid && fields == 127;
  if (valid) {
    const auto field = [root](const char* name) { return cJSON_GetObjectItemCaseSensitive(root, name); };
    uint32_t version = 0, distance = 0;
    const cJSON* cm = field("distance_cm");
    const cJSON* age = field("sample_age_ms");
    const cJSON* status = field("status");
    const cJSON* source = field("source");
    valid = jsonUint(field("v"), version) && version == 1 && jsonUint(field("seq"), sample.seq, 1) &&
            cJSON_IsBool(field("rover_connected")) && cJSON_IsString(status) && cJSON_IsString(source);
    sample.roverConnected = cJSON_IsTrue(field("rover_connected"));
    sample.hasDistance = !cJSON_IsNull(cm);
    sample.hasAge = !cJSON_IsNull(age);
    valid = valid && (!sample.hasDistance || (jsonUint(cm, distance, 1) && distance <= 150)) &&
            (!sample.hasAge || jsonUint(age, sample.ageMs));
    sample.cm = static_cast<uint16_t>(distance);
    if (valid) {
      if (!strcmp(status->valuestring, "live")) sample.status = Status::Live;
      else if (!strcmp(status->valuestring, "stale")) sample.status = Status::Stale;
      else if (!strcmp(status->valuestring, "invalid")) sample.status = Status::Invalid;
      else if (!strcmp(status->valuestring, "unavailable")) sample.status = Status::Unavailable;
      else valid = false;
      if (!strcmp(source->valuestring, "rover")) sample.source = Source::Rover;
      else if (!strcmp(source->valuestring, "demo")) sample.source = Source::Demo;
      else valid = false;
      if (sample.status == Status::Live && (!sample.hasDistance || !sample.hasAge)) valid = false;
      if ((sample.status == Status::Invalid || sample.status == Status::Unavailable) && sample.hasDistance) valid = false;
    }
  }
  cJSON_Delete(root);
  return valid;
}
}  // namespace bullseye
