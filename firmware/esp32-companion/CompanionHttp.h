#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <ctype.h>
#include <limits.h>

namespace companion {
enum class Command : uint8_t { Invalid, Status, Squeeze, Release, Home, Stop, Relax };
inline const char* commandName(Command command) {
  switch (command) {
    case Command::Status: return "status";
    case Command::Squeeze: return "squeeze";
    case Command::Release: return "release";
    case Command::Home: return "home";
    case Command::Stop: return "stop";
    case Command::Relax: return "relax";
    default: return "invalid";
  }
}
inline Command commandFrom(const char* name) {
  for (uint8_t i = 1; i <= 6; ++i) if (!strcmp(name, commandName(static_cast<Command>(i)))) return static_cast<Command>(i);
  return Command::Invalid;
}
inline bool unsigned32(const char* text, uint32_t& value) {
  if (!text || !*text) return false;
  uint32_t result = 0;
  for (const char* c = text; *c; ++c) {
    if (*c < '0' || *c > '9') return false;
    const uint32_t digit = *c - '0';
    if (result > (UINT32_MAX - digit) / 10) return false;
    result = result * 10 + digit;
  }
  value = result; return true;
}
inline bool equalsIgnoreCase(const char* first, const char* second) {
  while (*first && *second) {
    if (tolower(static_cast<unsigned char>(*first++)) != tolower(static_cast<unsigned char>(*second++))) return false;
  }
  return *first == *second;
}

struct Request {
  Command command = Command::Invalid;
  uint32_t boot = 0, sequence = 0;
  int error = 400;
};

// Small fixed-endpoint HTTP/1.x decoder. No body, chunked transfer, keep-alive,
// dynamic allocation, blocking reads or unbounded request processing.
class HttpDecoder {
 public:
  static constexpr size_t capacity = 1536;
  static constexpr uint32_t timeoutMs = 500;
  void reset(uint32_t now) { length_ = 0; ready_ = false; error_ = 0; started_ = now; }
  bool expired(uint32_t now) const { return static_cast<uint32_t>(now - started_) >= timeoutMs; }
  bool ready() const { return ready_; }
  void feed(const uint8_t* data, size_t count) {
    for (size_t i = 0; i < count && !ready_; ++i) {
      const uint8_t c = data[i];
      if ((c < 32 && c != '\r' && c != '\n' && c != '\t') || c > 126) { error_ = 400; ready_ = true; break; }
      if (length_ == capacity - 1) { error_ = 431; ready_ = true; break; }
      text_[length_++] = static_cast<char>(c);
      text_[length_] = '\0';
      if (length_ >= 4 && !memcmp(text_ + length_ - 4, "\r\n\r\n", 4)) ready_ = true;
    }
  }
  Request decode() {
    Request result;
    if (!ready_) return result;
    if (error_) { result.error = error_; return result; }
    char* end = strstr(text_, "\r\n");
    if (!end || end - text_ > 224) return result;
    *end = '\0';
    char method[8], path[192], version[16], extra;
    if (sscanf(text_, "%7s %191s %15s %c", method, path, version, &extra) != 3 ||
        (strcmp(version, "HTTP/1.1") && strcmp(version, "HTTP/1.0"))) return result;
    bool contentLengthSeen = false;
    for (char* header = end + 2; *header;) {
      char* next = strstr(header, "\r\n");
      if (!next) return result;
      if (next == header) break;
      *next = '\0';
      char* colon = strchr(header, ':');
      if (!colon || colon == header || *header == ' ' || *header == '\t') return result;
      *colon = '\0';
      char* value = colon + 1;
      while (*value == ' ' || *value == '\t') ++value;
      char* tail = value + strlen(value);
      while (tail > value && (tail[-1] == ' ' || tail[-1] == '\t')) *--tail = '\0';
      if (equalsIgnoreCase(header, "Transfer-Encoding")) return result;
      if (equalsIgnoreCase(header, "Content-Length")) {
        uint32_t length = 0;
        if (contentLengthSeen || !unsigned32(value, length)) return result;
        contentLengthSeen = true;
        if (length != 0) { result.error = 413; return result; }
      }
      header = next + 2;
    }
    if (!strcmp(path, "/status")) {
      if (strcmp(method, "GET")) { result.error = 405; return result; }
      result.command = Command::Status; result.error = 0; return result;
    }
    constexpr const char* prefix = "/command?";
    if (strncmp(path, prefix, strlen(prefix))) { result.error = 404; return result; }
    if (strcmp(method, "POST")) { result.error = 405; return result; }
    unsigned fields = 0;
    for (char* pair = path + strlen(prefix); pair && *pair;) {
      char* next = strchr(pair, '&'); if (next) *next++ = '\0';
      char* equal = strchr(pair, '='); if (!equal) return result;
      *equal++ = '\0';
      unsigned field;
      if (!strcmp(pair, "name")) { field = 1; result.command = commandFrom(equal); }
      else if (!strcmp(pair, "boot")) { field = 2; if (!unsigned32(equal, result.boot)) return result; }
      else if (!strcmp(pair, "seq")) { field = 4; if (!unsigned32(equal, result.sequence)) return result; }
      else return result;
      if (fields & field) return result;
      fields |= field; pair = next;
    }
    if (result.command == Command::Invalid || result.command == Command::Status) return result;
    if (fields != 7 || !result.boot || !result.sequence) { result.error = 409; return result; }
    result.error = 0; return result;
  }
 private:
  char text_[capacity] = {};
  size_t length_ = 0;
  uint32_t started_ = 0;
  bool ready_ = false;
  int error_ = 0;
};

// A newer STOP invalidates older packets even if Wi-Fi delivers them late.
class CommandOrder {
 public:
  explicit CommandOrder(uint32_t boot = 1) : boot_(boot ? boot : 1) {}
  void begin(uint32_t boot) { boot_ = boot ? boot : 1; sequence_ = 0; }
  bool accept(uint32_t boot, uint32_t sequence) {
    if (boot != boot_ || sequence == 0 || sequence <= sequence_) return false;
    sequence_ = sequence; return true;
  }
  void fence() { if (sequence_ < UINT32_MAX) ++sequence_; }
  uint32_t boot() const { return boot_; }
  uint32_t sequence() const { return sequence_; }
 private:
  uint32_t boot_, sequence_ = 0;
};
}  // namespace companion
