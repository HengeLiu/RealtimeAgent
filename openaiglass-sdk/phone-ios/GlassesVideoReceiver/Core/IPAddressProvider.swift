import Foundation

/// 本机 IPv4 地址提供器。
///
/// 主要功能：
/// 1. 枚举当前 iPhone 可用的 IPv4 地址。
/// 2. 过滤回环地址，便于展示给眼镜端连接。
enum IPAddressProvider {
    /// 读取可用 IPv4 地址列表。
    ///
    /// 返回值：
    /// 1. 去重后的 IPv4 地址数组。
    static func loadIPv4Addresses() -> [String] {
        var addresses: [String] = []
        var pointer: UnsafeMutablePointer<ifaddrs>?

        guard getifaddrs(&pointer) == 0, let firstAddress = pointer else {
            return []
        }
        defer { freeifaddrs(firstAddress) }

        var currentPointer: UnsafeMutablePointer<ifaddrs>? = firstAddress
        while let interface = currentPointer?.pointee {
            defer { currentPointer = interface.ifa_next }

            let family = interface.ifa_addr.pointee.sa_family
            guard family == UInt8(AF_INET) else {
                continue
            }

            let interfaceName = String(cString: interface.ifa_name)
            guard interfaceName != "lo0" else {
                continue
            }

            var hostBuffer = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            let result = getnameinfo(
                interface.ifa_addr,
                socklen_t(interface.ifa_addr.pointee.sa_len),
                &hostBuffer,
                socklen_t(hostBuffer.count),
                nil,
                0,
                NI_NUMERICHOST
            )
            guard result == 0 else {
                continue
            }
            addresses.append(String(cString: hostBuffer))
        }

        return Array(Set(addresses)).sorted()
    }
}
