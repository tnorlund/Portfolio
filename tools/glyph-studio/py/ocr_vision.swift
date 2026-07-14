import Foundation
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count >= 2 else { fputs("usage: ocr <image>\n", stderr); exit(1) }
let path = args[1]
guard let img = NSImage(contentsOfFile: path),
      let tiff = img.tiffRepresentation,
      let bmp = NSBitmapImageRep(data: tiff),
      let cg = bmp.cgImage else {
    fputs("cannot load \(path)\n", stderr); exit(1)
}
let W = Double(cg.width), H = Double(cg.height)
let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.usesLanguageCorrection = false
req.recognitionLanguages = ["en-US"]
let handler = VNImageRequestHandler(cgImage: cg, options: [:])
do { try handler.perform([req]) } catch { fputs("ocr err \(error)\n", stderr); exit(1) }
var lines: [[String: Any]] = []
for obs in (req.results ?? []) {
    guard let cand = obs.topCandidates(1).first else { continue }
    let bb = obs.boundingBox // normalized, origin bottom-left
    let x = bb.origin.x * W
    let y = (1.0 - bb.origin.y - bb.size.height) * H // convert to top-left
    let w = bb.size.width * W
    let h = bb.size.height * H
    lines.append(["text": cand.string, "conf": cand.confidence,
                  "x": x, "y": y, "w": w, "h": h])
}
let out: [String: Any] = ["path": path, "count": lines.count, "lines": lines]
let data = try! JSONSerialization.data(withJSONObject: out, options: [.prettyPrinted])
print(String(data: data, encoding: .utf8)!)
