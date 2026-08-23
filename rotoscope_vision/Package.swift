// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "RotoscopeVision",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "rotoscope-vision", targets: ["RotoscopeVisionCLI"]),
        .library(name: "RotoscopeVisionCore", targets: ["RotoscopeVisionCore"]),
    ],
    targets: [
        .target(
            name: "RotoscopeVisionCore",
            path: "Sources/RotoscopeVisionCore",
            swiftSettings: [.unsafeFlags(["-Ounchecked"], .when(configuration: .release))]
        ),
        .executableTarget(
            name: "RotoscopeVisionCLI",
            dependencies: ["RotoscopeVisionCore"],
            path: "Sources/RotoscopeVisionCLI"
        ),
        .testTarget(
            name: "RotoscopeVisionCoreTests",
            dependencies: ["RotoscopeVisionCore"],
            path: "Tests/RotoscopeVisionCoreTests"
        ),
    ]
)
