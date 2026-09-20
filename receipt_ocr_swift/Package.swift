// swift-tools-version: 6.0
import PackageDescription

// The Core AI LayoutLM backend is compiled only on request. Its API
// (CoreAIRuntime, re-exported by CoreAI) is still beta and only the macOS 27
// SDK has it; CI's macOS runner does not. Keep the default build independent
// of it. Enable with:
//   RECEIPT_OCR_COREAI=1 swift build -c release
let coreAIEnabled = Context.environment["RECEIPT_OCR_COREAI"] == "1"
let coreSwiftSettings: [SwiftSetting] = coreAIEnabled ? [.define("RECEIPT_OCR_COREAI")] : []

let package = Package(
    name: "receipt_ocr_swift",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "receipt-ocr", targets: ["ReceiptOCRCLI"]),
        .library(name: "ReceiptOCRCore", targets: ["ReceiptOCRCore"]),
    ],
    dependencies: [
        .package(url: "https://github.com/apple/swift-argument-parser", from: "1.3.0"),
            .package(url: "https://github.com/soto-project/soto", from: "7.15.0"),
        .package(url: "https://github.com/apple/swift-log", from: "1.5.3")
    ],
    targets: [
        .target(
            name: "ReceiptOCRCore",
            dependencies: [
                .product(name: "SotoS3", package: "soto"),
                .product(name: "SotoSQS", package: "soto"),
                .product(name: "SotoDynamoDB", package: "soto"),
                .product(name: "Logging", package: "swift-log"),
            ],
            resources: [
                .copy("LineItems/block_role_priors_v2.json"),
                .copy("Sections/section_order_priors_v2.json")
            ],
            swiftSettings: coreSwiftSettings
        ),
        .executableTarget(
            name: "ReceiptOCRCLI",
            dependencies: [
                "ReceiptOCRCore",
                .product(name: "ArgumentParser", package: "swift-argument-parser"),
                .product(name: "Logging", package: "swift-log"),
            ]
        ),
        .testTarget(
            name: "ReceiptOCRCoreTests",
            dependencies: ["ReceiptOCRCore"],
            resources: [.copy("Fixtures")]
        ),
        .testTarget(
            name: "IntegrationTests",
            dependencies: [
                "ReceiptOCRCore",
                .product(name: "SotoS3", package: "soto"),
                .product(name: "SotoSQS", package: "soto"),
                .product(name: "SotoDynamoDB", package: "soto")
            ]
        ),
    ],
    swiftLanguageModes: [.v5]
)

