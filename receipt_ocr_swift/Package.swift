// swift-tools-version: 5.10
import PackageDescription

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
        .package(url: "https://github.com/soto-project/soto", from: "6.8.0"),
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
            ]
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
            dependencies: ["ReceiptOCRCore"]
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
    ]
)


