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
fun WebSocketDebugScreen(viewModel: MainViewModel, uiState: PhoneUiState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        // WebSocket 状态
        DebugCard {
            SectionTitle("WebSocket 连接状态")
            Spacer(Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column {
                    Text("Control WS", color = TextSecondary, fontSize = 11.sp)
                    Text(
                        uiState.controlState,
                        color = if (uiState.isControlConnected) AccentGreen else AccentRed,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold
                    )
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text("Stream WS", color = TextSecondary, fontSize = 11.sp)
                    Text(
                        uiState.streamState,
                        color = if (uiState.isStreamConnected) AccentGreen else AccentRed,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold
                    )
                }
            }

            Spacer(Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text("Control URL:", color = TextSecondary, fontSize = 10.sp)
                Text(
                    "${uiState.serverUrl}/ws/control",
                    color = AccentBlue,
                    fontSize = 10.sp,
                    fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace
                )
            }
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text("Stream URL:", color = TextSecondary, fontSize = 10.sp)
                Text(
                    "${uiState.serverUrl}/ws/stream",
                    color = AccentBlue,
                    fontSize = 10.sp,
                    fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace
                )
            }
        }

        // 自定义事件发送
        DebugCard {
            SectionTitle("发送自定义事件")
            Spacer(Modifier.height(8.dp))

            var customEventName by remember { mutableStateOf("") }
            var customPayload by remember { mutableStateOf("{}") }

            OutlinedTextField(
                value = customEventName,
                onValueChange = { customEventName = it },
                label = { Text("事件名称", color = TextSecondary) },
                placeholder = { Text("control.device.heartbeat.received", color = TextSecondary) },
                singleLine = true,
                colors = darkTextFieldColors(),
                modifier = Modifier.fillMaxWidth()
            )

            Spacer(Modifier.height(8.dp))

            OutlinedTextField(
                value = customPayload,
                onValueChange = { customPayload = it },
                label = { Text("Payload (JSON)", color = TextSecondary) },
                colors = darkTextFieldColors(),
                modifier = Modifier
                    .fillMaxWidth()
                    .height(100.dp),
                maxLines = 5
            )

            Spacer(Modifier.height(8.dp))

            Button(
                onClick = { viewModel.sendCustomEvent(customEventName, customPayload) },
                enabled = uiState.isConnected && customEventName.isNotEmpty(),
                colors = ButtonDefaults.buttonColors(containerColor = AccentBlue),
                modifier = Modifier.fillMaxWidth()
            ) {
                Icon(Icons.Default.Send, null, Modifier.size(16.dp))
                Spacer(Modifier.width(6.dp))
                Text("发送事件")
            }
        }

        // 预设事件快捷发送
        DebugCard {
            SectionTitle("预设事件快捷发送")
            Spacer(Modifier.height(8.dp))

            val presetEvents = listOf(
                "control.device.heartbeat.received" to AccentGreen,
                "control.device.register.requested" to AccentBlue,
                "stream.input.opened" to AccentYellow,
                "stream.input.closed" to AccentRed,
                "command.accepted" to AccentGreen,
                "command.completed" to AccentBlue,
                "command.failed" to AccentRed
            )

            presetEvents.chunked(2).forEach { row ->
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    row.forEach { (eventName, color) ->
                        OutlinedButton(
                            onClick = { viewModel.sendPresetEvent(eventName) },
                            enabled = uiState.isConnected,
                            colors = ButtonDefaults.outlinedButtonColors(contentColor = color),
                            modifier = Modifier.weight(1f)
                        ) {
                            Text(
                                eventName.removePrefix("control.").removePrefix("stream.").removePrefix("command."),
                                fontSize = 10.sp,
                                fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace
                            )
                        }
                    }
                }
                Spacer(Modifier.height(4.dp))
            }
        }

        // 最近发送/接收的原始消息
        DebugCard {
            SectionTitle("最近消息 (${uiState.rawMessages.size})")
            Spacer(Modifier.height(4.dp))

            if (uiState.rawMessages.isEmpty()) {
                Text("暂无消息", color = TextSecondary, fontSize = 12.sp)
            } else {
                uiState.rawMessages.takeLast(10).forEach { msg ->
                    val msgColor = if (msg.startsWith(">>>")) AccentGreen else AccentBlue
                    Text(
                        msg,
                        color = msgColor,
                        fontSize = 10.sp,
                        fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace,
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(BorderColor.copy(alpha = 0.2f), RoundedCornerShape(4.dp))
                            .padding(6.dp)
                    )
                    Spacer(Modifier.height(4.dp))
                }
            }
        }
    }
}