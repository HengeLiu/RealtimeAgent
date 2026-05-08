import Foundation

/// 手机视觉任务优先级。
///
/// 主要功能：
/// 1. 把服务端下发的数字或字符串优先级归一化。
/// 2. 为资源抢占和帧投递排序提供可比较值。
enum VisionTaskPriority: Int, Comparable {
    case background = 0
    case normal = 10
    case foreground = 50
    case critical = 100

    static func < (lhs: VisionTaskPriority, rhs: VisionTaskPriority) -> Bool {
        lhs.rawValue < rhs.rawValue
    }

    /// 从任务参数中解析优先级。
    ///
    /// 参数：
    /// 1. `value`：可能是字符串或整数的优先级。
    ///
    /// 返回值：
    /// 1. 归一化后的优先级，无法识别时返回 `.normal`。
    static func parse(_ value: Any?) -> VisionTaskPriority {
        if let priority = value as? Int {
            if priority >= VisionTaskPriority.critical.rawValue {
                return .critical
            }
            if priority >= VisionTaskPriority.foreground.rawValue {
                return .foreground
            }
            if priority <= VisionTaskPriority.background.rawValue {
                return .background
            }
            return .normal
        }
        guard let text = value as? String else {
            return .normal
        }
        switch text.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "critical":
            return .critical
        case "foreground", "high":
            return .foreground
        case "background", "low":
            return .background
        default:
            return .normal
        }
    }
}

/// 手机视觉功耗模式。
///
/// 主要功能：
/// 1. 描述 iOS 运行时当前的资源保护状态。
/// 2. 为后台、低电量和过热场景提供统一降级语义。
enum VisionPowerMode: String {
    case normal
    case lowPower = "low_power"
    case thermalThrottled = "thermal_throttled"
    case background

    /// 从任务参数中解析功耗模式。
    static func parse(_ value: Any?) -> VisionPowerMode {
        guard let text = value as? String else {
            return .normal
        }
        return VisionPowerMode(rawValue: text) ?? .normal
    }
}

/// 手机视觉任务资源策略。
///
/// 主要功能：
/// 1. 表达任务处理帧率、最大帧数、优先级和模型资源需求。
/// 2. 兼容服务端旧参数和 `vision_policy` 新参数。
struct VisionTaskPolicy: Equatable {
    var minFrameIntervalMS: Int
    var maxFrames: Int?
    var priority: VisionTaskPriority
    var emitOverloadEvents: Bool
    var requiresExclusiveModel: Bool
    var allowsFrameSharing: Bool
    var allowsPreemption: Bool
    var powerMode: VisionPowerMode

    static let `default` = VisionTaskPolicy(
        minFrameIntervalMS: 0,
        maxFrames: nil,
        priority: .normal,
        emitOverloadEvents: true,
        requiresExclusiveModel: false,
        allowsFrameSharing: false,
        allowsPreemption: true,
        powerMode: .normal
    )

    /// 从任务启动参数解析视觉资源策略。
    ///
    /// 参数：
    /// 1. `params`：服务端下发的手机任务参数。
    ///
    /// 返回值：
    /// 1. 归一化后的资源策略。
    static func parse(params: [String: Any]) -> VisionTaskPolicy {
        let nested = params["vision_policy"] as? [String: Any] ?? [:]
        let hasVisionPolicy = params["vision_policy"] != nil
        func value(_ key: String) -> Any? {
            nested[key] ?? params[key]
        }

        let minInterval = value("min_frame_interval_ms") as? Int
            ?? value("frame_interval_ms") as? Int
            ?? VisionTaskPolicy.default.minFrameIntervalMS
        let maxFramesValue = value("max_frames") as? Int
        let priority = VisionTaskPriority.parse(value("priority"))
        let emitOverload = value("emit_overload_events") as? Bool ?? true
        let exclusive = value("requires_exclusive_model") as? Bool ?? hasVisionPolicy
        let sharing = value("allows_frame_sharing") as? Bool ?? false
        let preemption = value("allows_preemption") as? Bool ?? true
        let powerMode = VisionPowerMode.parse(value("power_mode"))

        var policy = VisionTaskPolicy(
            minFrameIntervalMS: max(0, minInterval),
            maxFrames: maxFramesValue.map { max(0, $0) },
            priority: priority,
            emitOverloadEvents: emitOverload,
            requiresExclusiveModel: exclusive,
            allowsFrameSharing: sharing,
            allowsPreemption: preemption,
            powerMode: powerMode
        )
        policy.applyPowerMode()
        return policy
    }

    /// 根据功耗模式调整策略。
    ///
    /// 主要逻辑：
    /// 1. 后台或低电量时降低低优先级任务帧率。
    /// 2. 过热时进一步降低非关键任务帧率。
    private mutating func applyPowerMode() {
        guard priority != .critical else {
            return
        }
        switch powerMode {
        case .normal:
            return
        case .background, .lowPower:
            minFrameIntervalMS = max(minFrameIntervalMS, 1000)
        case .thermalThrottled:
            minFrameIntervalMS = max(minFrameIntervalMS, 2000)
        }
    }
}

/// 视觉资源系统事件。
///
/// 主要功能：
/// 1. 记录资源授予、拒绝、抢占、降级和过载。
/// 2. 后续可直接转换为服务端任务事件。
struct VisionResourceEvent {
    let eventName: String
    let reason: String
    let taskID: String
    let taskType: String
    let streamID: String
    let sequence: Int?
    let framesProcessed: Int
    let framesDropped: Int

    /// 转成任务事件负载。
    var payload: [String: Any] {
        var result: [String: Any] = [
            "reason": reason,
            "task_id": taskID,
            "task_type": taskType,
            "stream_id": streamID,
            "frames_processed": framesProcessed,
            "frames_dropped": framesDropped,
        ]
        if let sequence {
            result["sequence"] = sequence
        }
        return result
    }
}

/// 手机视觉资源租约。
///
/// 主要功能：
/// 1. 记录任务当前持有的资源策略。
/// 2. 记录帧处理计数和最近投递时间，用于限流和过载判断。
private struct VisionTaskLease {
    let taskID: String
    let taskType: String
    let streamID: String
    let policy: VisionTaskPolicy
    var framesProcessed = 0
    var framesDropped = 0
    var lastFrameAt: Date?
}

/// 视觉任务启动时的资源决策。
struct VisionTaskStartDecision {
    let granted: Bool
    let events: [VisionResourceEvent]
    let preemptedTaskIDs: [String]
}

/// 单帧投递资源决策。
struct VisionFrameDispatchDecision {
    let targetTaskIDs: [String]
    let events: [VisionResourceEvent]
}

/// 真 iOS 手机视觉资源协调器。
///
/// 主要功能：
/// 1. 为手机视觉任务分配模型资源租约。
/// 2. 根据 `vision_policy` 做帧率限制、最大帧数限制和过载事件。
/// 3. 根据优先级做任务拒绝、抢占和降级。
final class VisionResourceCoordinator {
    private let maxExclusiveModelSlots: Int
    private var leasesByTaskID: [String: VisionTaskLease] = [:]
    private var activationOrder: [String] = []

    /// 创建资源协调器。
    ///
    /// 参数：
    /// 1. `maxExclusiveModelSlots`：可同时独占模型资源的任务数量。
    init(maxExclusiveModelSlots: Int = 1) {
        self.maxExclusiveModelSlots = max(1, maxExclusiveModelSlots)
    }

    /// 启动任务并申请视觉资源租约。
    ///
    /// 参数：
    /// 1. `taskID/taskType/streamID`：任务身份和视频流。
    /// 2. `params`：服务端下发的任务参数。
    ///
    /// 返回值：
    /// 1. 启动决策，包含是否授予、抢占任务和资源事件。
    func startTask(
        taskID: String,
        taskType: String,
        streamID: String,
        params: [String: Any]
    ) -> VisionTaskStartDecision {
        let policy = VisionTaskPolicy.parse(params: params)
        let occupiedLeases = leasesByTaskID.values.filter(\.policy.requiresExclusiveModel)
        var events: [VisionResourceEvent] = []
        var preemptedTaskIDs: [String] = []

        if policy.requiresExclusiveModel, occupiedLeases.count >= maxExclusiveModelSlots {
            let candidates = occupiedLeases
                .filter { policy.priority > $0.policy.priority && policy.allowsPreemption }
                .sorted { $0.policy.priority < $1.policy.priority }
            let requiredPreemptions = occupiedLeases.count - maxExclusiveModelSlots + 1
            if candidates.count >= requiredPreemptions {
                for lease in candidates.prefix(requiredPreemptions) {
                    leasesByTaskID.removeValue(forKey: lease.taskID)
                    activationOrder.removeAll { $0 == lease.taskID }
                    preemptedTaskIDs.append(lease.taskID)
                    events.append(makeEvent(
                        name: "vision.task.preempted",
                        reason: "higher_priority_task_started",
                        lease: lease,
                        sequence: nil
                    ))
                }
            } else {
                let deniedLease = VisionTaskLease(
                    taskID: taskID,
                    taskType: taskType,
                    streamID: streamID,
                    policy: policy
                )
                events.append(makeEvent(
                    name: "vision.resource.denied",
                    reason: "exclusive_model_slot_unavailable",
                    lease: deniedLease,
                    sequence: nil
                ))
                return VisionTaskStartDecision(granted: false, events: events, preemptedTaskIDs: [])
            }
        }

        var lease = VisionTaskLease(taskID: taskID, taskType: taskType, streamID: streamID, policy: policy)
        leasesByTaskID[taskID] = lease
        activationOrder.removeAll { $0 == taskID }
        activationOrder.append(taskID)
        events.append(makeEvent(name: "vision.resource.lease_granted", reason: "task_started", lease: lease, sequence: nil))
        if policy.powerMode != .normal, policy.priority != .critical {
            lease.framesDropped = 0
            events.append(makeEvent(name: "vision.task.degraded", reason: "power_policy_applied", lease: lease, sequence: nil))
        }
        return VisionTaskStartDecision(granted: true, events: events, preemptedTaskIDs: preemptedTaskIDs)
    }

    /// 停止任务并释放资源租约。
    func stopTask(taskID: String) {
        leasesByTaskID.removeValue(forKey: taskID)
        activationOrder.removeAll { $0 == taskID }
    }

    /// 为当前视频帧计算应投递的任务。
    ///
    /// 参数：
    /// 1. `sequence`：帧序号。
    /// 2. `now`：当前时间，测试可注入固定时间。
    ///
    /// 返回值：
    /// 1. 可接收帧的任务编号和资源事件。
    func resolveFrameRecipients(sequence: Int, now: Date = Date()) -> VisionFrameDispatchDecision {
        var events: [VisionResourceEvent] = []
        let candidateTaskIDs = currentCandidateTaskIDs()
        var targetTaskIDs: [String] = []

        for taskID in candidateTaskIDs {
            guard var lease = leasesByTaskID[taskID] else {
                continue
            }
            if let maxFrames = lease.policy.maxFrames, maxFrames > 0, lease.framesProcessed >= maxFrames {
                lease.framesDropped += 1
                leasesByTaskID[taskID] = lease
                if lease.policy.emitOverloadEvents {
                    events.append(makeEvent(
                        name: "vision.task.overloaded",
                        reason: "max_frames_reached",
                        lease: lease,
                        sequence: sequence
                    ))
                }
                continue
            }
            if let lastFrameAt = lease.lastFrameAt,
               lease.policy.minFrameIntervalMS > 0,
               now.timeIntervalSince(lastFrameAt) * 1000 < Double(lease.policy.minFrameIntervalMS) {
                lease.framesDropped += 1
                leasesByTaskID[taskID] = lease
                if lease.policy.emitOverloadEvents {
                    events.append(makeEvent(
                        name: "vision.task.overloaded",
                        reason: "frame_rate_limited",
                        lease: lease,
                        sequence: sequence
                    ))
                }
                continue
            }
            lease.framesProcessed += 1
            lease.lastFrameAt = now
            leasesByTaskID[taskID] = lease
            targetTaskIDs.append(taskID)
        }

        return VisionFrameDispatchDecision(targetTaskIDs: targetTaskIDs, events: events)
    }

    /// 当前任务是否拥有资源租约。
    func hasLease(taskID: String) -> Bool {
        leasesByTaskID[taskID] != nil
    }

    /// 选择本帧候选任务。
    ///
    /// 主要逻辑：
    /// 1. 如果任一任务允许共享帧，则所有租约任务都按优先级参与。
    /// 2. 默认保持旧行为，只投递给最新启动的任务。
    private func currentCandidateTaskIDs() -> [String] {
        let leases = leasesByTaskID.values
        if leases.contains(where: \.policy.allowsFrameSharing) {
            return leases.sorted { lhs, rhs in
                if lhs.policy.priority == rhs.policy.priority {
                    let lhsIndex = activationOrder.firstIndex(of: lhs.taskID) ?? 0
                    let rhsIndex = activationOrder.firstIndex(of: rhs.taskID) ?? 0
                    return lhsIndex > rhsIndex
                }
                return lhs.policy.priority > rhs.policy.priority
            }.map(\.taskID)
        }
        return activationOrder.last.map { [$0] } ?? []
    }

    /// 创建标准资源事件。
    private func makeEvent(name: String, reason: String, lease: VisionTaskLease, sequence: Int?) -> VisionResourceEvent {
        VisionResourceEvent(
            eventName: name,
            reason: reason,
            taskID: lease.taskID,
            taskType: lease.taskType,
            streamID: lease.streamID,
            sequence: sequence,
            framesProcessed: lease.framesProcessed,
            framesDropped: lease.framesDropped
        )
    }
}
