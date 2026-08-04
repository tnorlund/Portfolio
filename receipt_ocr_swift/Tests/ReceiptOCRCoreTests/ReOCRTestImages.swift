import Foundation
#if os(macOS)
import AppKit
import CoreGraphics
import CoreText

/// Shared synthetic-image helpers for the re-OCR preprocess tests.
enum ReOCRTestImages {

    /// Create an RGBA8 CGImage of the given size, handing the caller a
    /// CGContext (bottom-left origin) to draw into.
    static func makeImage(width: Int, height: Int, draw: (CGContext) -> Void) -> CGImage {
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        let context = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: width * 4,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        )!
        draw(context)
        return context.makeImage()!
    }

    /// Sample the (r, g, b, a) byte values of a pixel. Coordinates are
    /// top-left origin (row 0 = top of the image).
    static func pixel(in image: CGImage, x: Int, y: Int) -> (r: UInt8, g: UInt8, b: UInt8, a: UInt8) {
        let width = image.width
        let height = image.height
        var buffer = [UInt8](repeating: 0, count: width * height * 4)
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        let context = CGContext(
            data: &buffer,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: width * 4,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        )!
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        let offset = (y * width + x) * 4
        return (buffer[offset], buffer[offset + 1], buffer[offset + 2], buffer[offset + 3])
    }

    /// PNG-encode a CGImage (for feeding the worker's S3 mock).
    static func pngData(from image: CGImage) -> Data {
        let rep = NSBitmapImageRep(cgImage: image)
        return rep.representation(using: .png, properties: [:])!
    }

    /// White canvas with several lines of black receipt-like text, rendered
    /// with CoreText so Vision has real glyphs to find.
    static func makeTextImage(width: Int = 800, height: Int = 400) -> CGImage {
        makeImage(width: width, height: height) { ctx in
            ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
            ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
            let font = CTFontCreateWithName("Helvetica" as CFString, 32, nil)
            let lines = [
                "RECEIPT TOTAL 12.34",
                "SUBTOTAL 10.00 TAX 2.34",
                "THANK YOU FOR SHOPPING",
            ]
            for (index, text) in lines.enumerated() {
                let attributes: [CFString: Any] = [
                    kCTFontAttributeName: font,
                    kCTForegroundColorAttributeName: CGColor(red: 0, green: 0, blue: 0, alpha: 1),
                ]
                let attributed = CFAttributedStringCreate(
                    nil, text as CFString, attributes as CFDictionary
                )!
                let line = CTLineCreateWithAttributedString(attributed)
                ctx.textPosition = CGPoint(x: 60, y: CGFloat(height - 90 - index * 90))
                CTLineDraw(line, ctx)
            }
        }
    }
}
#endif
