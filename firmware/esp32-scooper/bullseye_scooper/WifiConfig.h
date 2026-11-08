#pragma once
// Wi-Fi settings: the scooper joins the rover's own ELEGOO access point, the same
// network the laptop uses for the camera and rover commands. Mission Control then
// sends squeeze/release/home/stop to http://192.168.4.50 (see the README).
#include <stdint.h>

namespace scooper {

// The rover camera's open access point (no password, channel 9). The name ends in the
// camera's chip ID; this is the team rover's, from docs/HANDOFF.md. Change it for
// another rover, and set SCOOPER_URL on the server if you change the address below.
constexpr const char* kWifiSsid = "ELEGOO-2436CBF61B44";
constexpr const char* kWifiPassword = "";  // Open network.

// Fixed address on the ELEGOO network (the camera is 192.168.4.1 and hands out
// addresses from .2 upward), so the server always knows where the scooper is.
constexpr uint8_t kScooperIp[4] = {192, 168, 4, 50};
constexpr uint8_t kGatewayIp[4] = {192, 168, 4, 1};
constexpr uint8_t kSubnetMask[4] = {255, 255, 255, 0};
constexpr uint16_t kHttpPort = 80;

constexpr uint32_t kWifiRetryMs = 10000;  // Retry joining while the rover is off or out of range.

}  // namespace scooper
