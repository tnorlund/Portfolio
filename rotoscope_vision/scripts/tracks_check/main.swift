// Standalone checks for the Foundation-only track components (no XCTest needed).
//   swiftc -O Sources/RotoscopeVisionCore/Engine.swift Sources/RotoscopeVisionCore/LucasKanade.swift \
//     Sources/RotoscopeVisionCore/ObjectModel.swift Sources/RotoscopeVisionCore/Params.swift \
//     Sources/RotoscopeVisionCore/FeatureTracker.swift Sources/RotoscopeVisionCore/BackgroundPlate.swift \
//     Sources/RotoscopeVisionCore/TrackClassifier.swift scripts/tracks_check/main.swift -o /tmp/tracks_check && /tmp/tracks_check
import Foundation
import simd

var failures = 0
func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    if condition() { print("ok   \(message)") } else { failures += 1; print("FAIL \(message)") }
}

// Deterministic textured image: sum of sinusoids plus a hashed speckle.
func texture(width: Int, height: Int, shiftX: Float, shiftY: Float) -> [UInt8] {
    var out = [UInt8](repeating: 0, count: width * height)
    for y in 0..<height {
        for x in 0..<width {
            let fx = Float(x) - shiftX, fy = Float(y) - shiftY
            var v = 128 + 50 * sin(fx * 0.35) * cos(fy * 0.27) + 30 * sin((fx + fy) * 0.5)
            let h = (Int(fx.rounded(.down)) &* 73856093) ^ (Int(fy.rounded(.down)) &* 19349663)
            v += Float((h >> 8) & 31) - 16
            out[y * width + x] = UInt8(max(0, min(255, v)))
        }
    }
    return out
}

// Smooth, shift-invariant texture for sub-pixel accuracy checks.
func smoothTexture(width: Int, height: Int, shiftX: Float, shiftY: Float) -> [UInt8] {
    var out = [UInt8](repeating: 0, count: width * height)
    for y in 0..<height {
        for x in 0..<width {
            let fx = Float(x) - shiftX, fy = Float(y) - shiftY
            let v = 128 + 50 * sin(fx * 0.35) * cos(fy * 0.27) + 30 * sin((fx + fy) * 0.5) + 20 * cos(fx * 0.9 - fy * 0.6)
            out[y * width + x] = UInt8(max(0, min(255, v)))
        }
    }
    return out
}

// 1. Lucas–Kanade recovers a sub-pixel shift.
do {
    let w = 64, h = 64
    let a = smoothTexture(width: w, height: h, shiftX: 0, shiftY: 0)
    let b = smoothTexture(width: w, height: h, shiftX: 0.37, shiftY: -0.22)  // content moved by (+0.37, −0.22)
    let grad = Engine.sobel(b, width: w, height: h)
    let p0 = SIMD2<Float>(31, 33)
    let template = LucasKanade.patch(a, width: w, height: h, at: p0, radius: 5)!
    let result = LucasKanade.refine(template: template, radius: 5, current: b, gradient: grad, width: w, height: h,
                                    start: p0, iterations: 12, minEigen: 10)
    let err = simd_distance(result.position, p0 + SIMD2<Float>(0.37, -0.22))
    check(result.converged && err < 0.08, String(format: "LK recovers a 0.37/−0.22 px shift (error %.3f px)", err))
    check(result.ssd < 6, String(format: "LK residual small on matching texture (%.2f)", result.ssd))
}

// 2. LK rejects a patch replaced by unrelated texture (high residual).
do {
    let w = 64, h = 64
    let a = texture(width: w, height: h, shiftX: 0, shiftY: 0)
    let b = texture(width: w, height: h, shiftX: 17.3, shiftY: 9.1)
    let grad = Engine.sobel(b, width: w, height: h)
    let p0 = SIMD2<Float>(31, 33)
    let template = LucasKanade.patch(a, width: w, height: h, at: p0, radius: 5)!
    let result = LucasKanade.refine(template: template, radius: 5, current: b, gradient: grad, width: w, height: h,
                                    start: p0, iterations: 12, minEigen: 10)
    check(result.ssd > 15 || !result.converged, String(format: "LK reports a poor match on unrelated texture (ssd %.1f)", result.ssd))
}

// 3. Similarity.fit recovers a known transform exactly; fitRobust ignores outliers.
do {
    let truth = Similarity(scale: 1.03, angle: 0.05, tx: 4.5, ty: -2.25)
    let from: [SIMD2<Float>] = [[10, 10], [60, 12], [35, 50], [80, 70], [20, 65]]
    let to = from.map { truth.apply($0) }
    let fit = Similarity.fit(from: from, to: to)!
    let e = from.map { simd_distance(fit.apply($0), truth.apply($0)) }.max()!
    check(e < 1e-3, String(format: "Similarity.fit exact on 5 points (max error %.5f)", e))
    var noisy = to
    noisy.append(truth.apply([50, 30]) + [40, -25])  // outliers
    noisy.append(truth.apply([70, 20]) + [-30, 33])
    let fromAll = from + [[50, 30], [70, 20]]
    let robust = Similarity.fitRobust(from: fromAll, to: noisy, threshold: 2)!
    let inliers = robust.inliers.reduce(0) { $0 + ($1 ? 1 : 0) }
    let e2 = from.map { simd_distance(robust.transform.apply($0), truth.apply($0)) }.max()!
    check(inliers == 5 && e2 < 0.05, String(format: "fitRobust ignores 2/7 outliers (inliers %d, error %.4f)", inliers, e2))
    let inv = truth.inverse * truth
    check(abs(inv.scale - 1) < 1e-5 && abs(inv.angle) < 1e-5 && inv.translationMagnitude < 1e-3, "Similarity inverse composes to identity")
}

// 4. Feature tracker: features on a moving texture keep their ids and follow the motion.
do {
    let w = 160, h = 120
    var params = Params()
    params.trackBudget = 200
    params.trackSpacing = 8
    params.trackMinScore = 50
    params.trackQuality = 0.002
    let tracker = FeatureTracker(width: w, height: h, params: params)
    var ids: [Int] = []
    for f in 0..<6 {
        let shift = Float(f) * 1.5
        let gray = texture(width: w, height: h, shiftX: shift, shiftY: shift * 0.5)
        let grad = Engine.sobel(gray, width: w, height: h)
        var rgba = [UInt8](repeating: 255, count: w * h * 4)
        for i in 0..<(w * h) { rgba[i * 4] = gray[i]; rgba[i * 4 + 1] = gray[i]; rgba[i * 4 + 2] = gray[i] }
        tracker.step(gray: gray, gradient: grad, rgba: rgba, forward: { $0 + SIMD2<Float>(1.5, 0.75) }, predict: { _ in nil }, detectMask: nil)
        if f == 0 { ids = tracker.tracks.map { $0.id } }
    }
    let survivors = tracker.tracks.filter { $0.status == .live && ids.contains($0.id) && $0.positions.count == 6 }
    var maxErr: Float = 0
    for t in survivors {
        let expected = t.positions[0] + SIMD2<Float>(7.5, 3.75)
        maxErr = max(maxErr, simd_distance(t.current, expected))
    }
    check(ids.count >= 40, "tracker detects a useful number of features (\(ids.count))")
    check(survivors.count >= ids.count * 7 / 10, "≥70 % of frame-0 tracks survive 5 frames of motion (\(survivors.count)/\(ids.count))")
    check(maxErr < 0.6, String(format: "surviving tracks follow the 1.5/0.75 px per-frame motion (max error %.2f px)", maxErr))
}

// 5. Classifier: static texture under a pan is background; a patch moving differently is not.
do {
    let w = 160, h = 120
    var params = Params()
    params.trackBudget = 300
    params.trackSpacing = 8
    params.trackMinScore = 50
    params.trackQuality = 0.002
    params.motionWindow = 4
    params.moveTolerance = 2
    let tracker = FeatureTracker(width: w, height: h, params: params)
    let classifier = TrackClassifier(width: w, height: h, params: params)
    let person = [UInt8](repeating: 0, count: w * h)
    for f in 0..<8 {
        let pan = Float(f) * 1.0
        var gray = texture(width: w, height: h, shiftX: pan, shiftY: 0)
        // A 30×30 patch in the lower-right moving the opposite way, twice as fast.
        let patch = texture(width: 30, height: 30, shiftX: -Float(f) * 2.0, shiftY: 0)
        for y in 0..<30 { for x in 0..<30 { gray[(80 + y) * w + 110 + x] = patch[y * 30 + x] } }
        let grad = Engine.sobel(gray, width: w, height: h)
        var rgba = [UInt8](repeating: 255, count: w * h * 4)
        for i in 0..<(w * h) { rgba[i * 4] = gray[i]; rgba[i * 4 + 1] = gray[i]; rgba[i * 4 + 2] = gray[i] }
        // Difference: zero on the panned background (plate agrees), large in the patch.
        var diff = [UInt8](repeating: 0, count: w * h)
        for y in 80..<110 { for x in 110..<140 { diff[y * w + x] = 120 } }
        tracker.step(gray: gray, gradient: grad, rgba: rgba, forward: { $0 + SIMD2<Float>(1, 0) }, predict: { _ in nil }, detectMask: nil)
        classifier.classify(tracker: tracker, frame: f, rgba: rgba, difference: diff, warpedPlate: nil, person: person, others: nil, registrarPrediction: nil)
    }
    let fit = classifier.lastStats.fit!
    check(abs(fit.transform.tx - 1) < 0.3 && abs(fit.transform.ty) < 0.3, String(format: "background consensus finds the 1 px pan (tx %.2f ty %.2f)", fit.transform.tx, fit.transform.ty))
    var bgOK = 0, bgTotal = 0, patchMoving = 0, patchTotal = 0
    for t in tracker.tracks where t.status == .live {
        let inPatch = t.current.x >= 112 && t.current.x < 138 && t.current.y >= 82 && t.current.y < 108
        if inPatch { patchTotal += 1; if t.label == .moving || t.label == .attached { patchMoving += 1 } }
        else { bgTotal += 1; if t.label == .background { bgOK += 1 } }
    }
    check(bgTotal > 0 && bgOK * 10 >= bgTotal * 9, "≥90 % of panned-background tracks labelled background (\(bgOK)/\(bgTotal))")
    check(patchTotal > 0 && patchMoving * 10 >= patchTotal * 7, "≥70 % of the counter-moving patch labelled moving (\(patchMoving)/\(patchTotal))")
}

print(failures == 0 ? "all checks passed" : "\(failures) check(s) failed")
exit(failures == 0 ? 0 : 1)
