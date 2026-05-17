package com.audiochat.phone.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@Composable
fun DeviceDebugScreen(viewModel: MainViewModel, uiState: PhoneUiState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        // 设备信息
        DebugCard {
            SectionTitle("设备信息")
            Spacer(Modifier.height(8.dp))

            DeviceInfoRow("设备 ID", uiState.deviceId)
            DeviceInfoRow("User ID", uiState.userId)
            DeviceInfoRow("设备名称", "android-phone")
            DeviceInfoRow("客户端类型", "android")
            DeviceInfoRow("SDK 版本", "1.0.0-android")
            DeviceInfoRow("协议版本", "audio-chat.v1")
            DeviceInfoRow("平台", "Android ${android.os.Build.VERSION.SDK_INT}")
            DeviceInfoRow("型号", "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}")
        }

        // 能力声明
        DebugCard {
            SectionTitle("能力声明 (supports)")
            Spacer(Modifier.height(8.dp))

            Text("Sensors:", color = AccentBlue, fontSize = 12.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(4.dp))

            // RGB Sensor
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(BorderColor.copy(alpha = 0.3f), RoundedCornerShape(6.dp))
                    .padding(10.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column {
                    Text("sensor.rgb", color = TextPrimary, fontSize = 13.sp, fontWeight = FontWeight.Medium)
                    Text("modes: single, continuous", color = TextSecondary, fontSize = 11.sp)
                    Text("format: jpeg | freq: 1Hz | samples: 1", color = TextSecondary, fontSize = 11.sp)
                }
                Icon(Icons.Default.CameraAlt, null, tint = AccentGreen, modifier = Modifier.size(24.dp))
            }

            Spacer(Modifier.height(8.dp))

            Text("Actuators:", color = AccentBlue, fontSize = 12.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(4.dp))

            // Vibrator Actuator
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(BorderColor.copy(alpha = 0.3f), RoundedCornerShape(6.dp))
                    .padding(10.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column {
                    Text("actuator.vibrator", color = TextPrimary, fontSize = 13.sp, fontWeight = FontWeight.Medium)
                    Text("commands: vibrate", color = TextSecondary, fontSize = 11.sp)
                }
                Icon(Icons.Default.Vibration, null, tint = AccentYellow, modifier = Modifier.size(24.dp))
            }
        }

        // Properties
        DebugCard {
            SectionTitle("设备属性 (properties)")
            Spacer(Modifier.height(8.dp))

            val props = mapOf(
                "device_role" to "phone",
                "endpoint.role.phone" to "true",
                "audio_chat.audio_input" to "sensor.mic",
                "audio_chat.audio_output" to "actuator.speaker"
            )

            props.forEach { (key, value) ->
                DeviceInfoRow(key, value)
            }
        }

        // 传感器状态
        DebugCard {
            SectionTitle("传感器实时状态")
            Spacer(Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly
            ) {
                SensorStatusCard(
                    name = "麦克风",
                    icon = Icons.Default.Mic,
                    isActive = uiState.isCapturingAudio,
                    detail = if (uiState.isCapturingAudio) "16kHz PCM" else "idle"
                )
                SensorStatusCard(
                    name = "摄像头",
                    icon = Icons.Default.CameraAlt,
                    isActive = uiState.isCameraActive,
                    detail = if (uiState.isCameraActive) "JPEG" else "idle"
                )
                SensorStatusCard(
                    name = "扬声器",
                    icon = Icons.Default.Speaker,
                    isActive = uiState.isSpeakerActive,
                    detail = if (uiState.isSpeakerActive) "24kHz PCM" else "idle"
                )
                SensorStatusCard(
                    name = "震动",
                    icon = Icons.Default.Vibration,
                    isActive = uiState.isVibrating,
                    detail = if (uiState.isVibrating) "active" else "idle"
                )
            }
        }

        // 测试功能
        DebugCard {
            SectionTitle("设备功能测试")
            Spacer(Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                OutlinedButton(
                    onClick = viewModel::testVibrate,
                    enabled = uiState.isRegistered,
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = AccentYellow),
                    modifier = Modifier.weight(1f)
                ) {
                    Icon(Icons.Default.Vibration, null, Modifier.size(16.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("测试震动", fontSize = 12.sp)
                }

                OutlinedButton(
                    onClick = viewModel::testSpeaker,
                    enabled = uiState.isRegistered,
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = AccentGreen),
                    modifier = Modifier.weight(1f)
                ) {
                    Icon(Icons.Default.Speaker, null, Modifier.size(16.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("测试扬声器", fontSize = 12.sp)
                }
            }
        }
    }
}

@Composable
fun DeviceInfoRow(label: String, value: String) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 3.dp),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(label, color = TextSecondary, fontSize = 12.sp)
        Text(
            value,
            color = TextPrimary,
            fontSize = 12.sp,
            fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace
        )
    }
}

@Composable
fun SensorStatusCard(name: String, icon: androidx.compose.ui.graphics.vector.ImageVector, isActive: Boolean, detail: String) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier
            .background(
                if (isActive) AccentGreen.copy(alpha = 0.1f) else BorderColor.copy(alpha = 0.3f),
                RoundedCornerShape(8.dp)
            )
            .padding(8.dp)
    ) {
        Icon(
            icon,
            null,
            tint = if (isActive) AccentGreen else TextSecondary,
            modifier = Modifier.size(20.dp)
        )
        Spacer(Modifier.height(4.dp))
        Text(name, color = TextPrimary, fontSize = 10.sp, fontWeight = FontWeight.Medium)
        Text(detail, color = if (isActive) AccentGreen else TextSecondary, fontSize = 9.sp)
    }
}