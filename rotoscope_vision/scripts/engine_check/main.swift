// Standalone engine check for machines without XCTest (Command Line Tools only).
// Mirrors Tests/RotoscopeVisionCoreTests/EngineTests.swift. Run:
//   swiftc -O Sources/RotoscopeVisionCore/Engine.swift scripts/engine_check/main.swift -o /tmp/engine_check && /tmp/engine_check
import Foundation

var failures = 0
func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    if condition() {
        print("ok   \(message)")
    } else {
        failures += 1
        print("FAIL \(message)")
    }
}

check(Engine.rec601Gray(72, 48, 40) == 54, "Rec. 601 gray matches the browser engine")
check(Engine.boxBlur([0, 90, 0, 90, 0], width: 5, height: 1, radius: 1) == [45, 30, 60, 30, 45], "box blur clamps edges")

var image = [UInt8](repeating: 0, count: 15)
for y in 0..<3 { for x in 3..<5 { image[y * 5 + x] = 200 } }
let gradient = Engine.sobel(image, width: 5, height: 3)
check(gradient.x[7] == 800 && gradient.y[7] == 0 && gradient.magnitude[7] == 200, "sobel sees a vertical edge")

check(Engine.tierQuota(budget: 10, quotas: TierValues(face: 0.3, body: 0.7, background: 0)) == [3, 7, 0], "tier quota 3/7/0")
check(Engine.tierQuota(budget: 7, quotas: TierValues(face: 1, body: 1, background: 1)) == [3, 2, 2], "tier quota remainder order")

do {
    let width = 6, height = 4
    var barrier = [UInt8](repeating: 0, count: width * height)
    for y in 0..<height { for x in 4..<6 { barrier[y * width + x] = 1 } }
    let result = Engine.watershed(
        gradient: [UInt8](repeating: 0, count: width * height), width: width, height: height,
        markers: [0, 3 * width + 3], barrier: barrier)
    var good = result.regionCount == 2
    for y in 0..<height { for x in 0..<width {
        let label = result.labels[y * width + x]
        good = good && (x >= 4 ? label == 0 : label != 0)
    } }
    check(good, "watershed labels everything but the barrier")
}

do {
    let out = Engine.colorize(
        rgba: [100, 0, 0, 255, 200, 0, 0, 255, 9, 9, 9, 255], labels: [1, 1, 0], count: 3, regionCount: 1,
        alpha: [255, 255, 255])
    check(Array(out[0..<4]) == [150, 0, 0, 255] && Array(out[4..<8]) == [150, 0, 0, 255] && out[11] == 0,
          "colorize averages regions and clears barrier alpha")
}

do {
    let width = 16, height = 12, count = width * height
    var rgba = [UInt8](repeating: 0, count: count * 4)
    var tiers = [UInt8](repeating: FocusTier.background.rawValue, count: count)
    var alpha = [UInt8](repeating: 0, count: count)
    for y in 0..<height { for x in 0..<width {
        let index = y * width + x
        let subject = x >= 4 && x < 12
        rgba[index * 4] = subject ? 200 : 20
        rgba[index * 4 + 1] = UInt8((x * 13 + y * 7) % 256)
        rgba[index * 4 + 2] = 60
        rgba[index * 4 + 3] = 255
        if subject { tiers[index] = FocusTier.body.rawValue; alpha[index] = 255 }
    } }
    var options = EngineOptions()
    options.markerBudget = 8
    options.blurRadius = 1
    let frame = Engine.process(
        rgba: rgba, width: width, height: height, tiers: tiers, alpha: alpha, removeBackground: true, options: options)
    var good = frame.regionCount > 0 && frame.markers.backgroundCount == 0
    for y in 0..<height { for x in 0..<width {
        let a = frame.rgba[(y * width + x) * 4 + 3]
        good = good && (x >= 4 && x < 12 ? a == 255 : a == 0)
    } }
    check(good, "process removes the background and keeps the subject opaque")
}

print(failures == 0 ? "all checks passed" : "\(failures) check(s) failed")
exit(failures == 0 ? 0 : 1)
