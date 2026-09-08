// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "VisionPortraitWorker",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "VisionPortraitCore", targets: ["VisionPortraitCore"]),
        .executable(name: "vision-portrait-worker", targets: ["VisionPortraitWorker"]),
    ],
    targets: [
        .target(name: "VisionPortraitCore"),
        .executableTarget(
            name: "VisionPortraitWorker",
            dependencies: ["VisionPortraitCore"]
        ),
        .testTarget(
            name: "VisionPortraitCoreTests",
            dependencies: ["VisionPortraitCore"]
        ),
    ]
)
