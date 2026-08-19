// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "RoastStudio",
    platforms: [.macOS(.v12)],
    targets: [
        .executableTarget(
            name: "RoastStudio",
            path: "Sources/RoastStudio"
        )
    ]
)
