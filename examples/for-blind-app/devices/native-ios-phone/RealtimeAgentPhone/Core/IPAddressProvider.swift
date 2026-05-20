import Foundation

/// 本机 IPv4 地址提供器。
///
/// 主要用途：iOS phone 启动本地相机接收服务后，需要把局域网内可连接的
/// `ws://<ip>:<port>/ws/camera` 地址注册给 server 或展示给 ESP32 端调试。
enum RealtimeAgentIPAddressProvider {
    static func loadIPv4Addresses() -> [String] {
        var addresses: [String] = []
        var pointer: UnsafeMutablePointer<ifaddrs>?

        guard getifaddrs(&pointer) == 0, let firstAddress = pointer else {
            return []
        }
        defer { freeifaddrs(firstAddress) }

        var current: UnsafeMutablePointer<ifaddrs>? = firstAddress
        while let interface = current?.pointee {
            defer { current = interface.ifa_next }
            guard interface.ifa_addr.pointee.sa_family == UInt8(AF_INET) else {
                continue
            }
            let name = String(cString: interface.ifa_name)
            guard name != "lo0" else {
                continue
            }

            var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            let result = getnameinfo(
                interface.ifa_addr,
                socklen_t(interface.ifa_addr.pointee.sa_len),
                &host,
                socklen_t(host.count),
                nil,
                0,
                NI_NUMERICHOST
            )
            if result == 0 {
                addresses.append(String(cString: host))
            }
        }
        return Array(Set(addresses)).sorted()
    }
}
