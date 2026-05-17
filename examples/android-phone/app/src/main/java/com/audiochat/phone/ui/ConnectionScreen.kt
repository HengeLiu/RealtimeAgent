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
fun ConnectionScreen(viewModel: MainViewModel, uiState: PhoneUiState) {
    Box(modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
        // 服务器配置
        DebugCard {
            SectionTitle("服务器配置")
            Spacer(Modifier.height(8.dp))

            OutlinedTextField(
                value = uiState.serverUrl,
                onValueChange = viewModel::updateServerUrl,
                label = { Text("Server URL", color = TextSecondary) },
                placeholder = { Text("http://192.168.x.x:8765", color = TextSecondary) },
                leadingIcon = { Icon(Icons.Default.Link, null, tint = AccentBlue) },
                singleLine = true,
                colors = darkTextFieldColors(),
                modifier = Modifier.fillMaxWidth()
            )

            Spacer(Modifier.height(8.dp))

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = uiState.userId,
                    onValueChange = viewModel::updateUserId,
                    label = { Text("User ID", color = TextSecondary) },
                    leadingIcon = { Icon(Icons.Default.Person, null, tint = AccentBlue) },
                    singleLine = true,
                    colors = darkTextFieldColors(),
                    modifier = Modifier.weight(1f)
                )
                OutlinedTextField(
                    value = uiState.deviceId,
                    onValueChange = viewModel::updateDeviceId,
                    label = { Text("Device ID", color = TextSecondary) },
                    leadingIcon = { Icon(Icons.Default.Smartphone, null, tint = AccentBlue) },
                    singleLine = true,
                    colors = darkTextFieldColors(),
                    modifier = Modifier.weight(1f)
                )
            }
        }

        // 连接状态
        DebugCard {
            SectionTitle("连接状态")
            Spacer(Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly
            ) {
                StatusIndicator(
                    label = "Control WS",
                    isActive = uiState.isControlConnected,
                    detail = uiState.controlState
                )
                StatusIndicator(
                    label = "Stream WS",
                    isActive = uiState.isStreamConnected,
                    detail = uiState.streamState
                )
                StatusIndicator(
                    label = "已注册",
                    isActive = uiState.isRegistered,
                    detail = if (uiState.isRegistered) "OK" else "-"
                )
                StatusIndicator(
                    label = "心跳",
                    isActive = uiState.lastHeartbeatTime > 0,
                    detail = if (uiState.lastHeartbeatTime > 0) {
                        "${(System.currentTimeMillis() - uiState.lastHeartbeatTime) / 1000}s前"
                    } else "-"
                )
            }

            Spacer(Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly
            ) {
                StatusIndicator(
                    label = "眼镜直连",
                    isActive = uiState.isPeerVideoConnected,
                    detail = if (uiState.isPeerVideoConnected) {
                        uiState.peerVideoClientIp.takeLast(10)
                    } else "idle"
                )
            }
        }

        // 连接按钮
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Button(
                onClick = viewModel::connect,
                enabled = !uiState.isConnected,
                colors = ButtonDefaults.buttonColors(containerColor = AccentGreen),
                modifier = Modifier.weight(1f)
            ) {
                Icon(Icons.Default.Link, null, Modifier.size(18.dp))
                Spacer(Modifier.width(6.dp))
                Text("连接并注册")
            }

            Button(
                onClick = viewModel::disconnect,
                enabled = uiState.isConnected,
                colors = ButtonDefaults.buttonColors(containerColor = AccentRed),
                modifier = Modifier.weight(1f)
            ) {
                Icon(Icons.Default.LinkOff, null, Modifier.size(18.dp))
                Spacer(Modifier.width(6.dp))
                Text("断开连接")
            }
        }

        // 统计信息
        DebugCard {
            SectionTitle("会话统计")
            Spacer(Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                StatItem("收到事件", "${uiState.eventsReceived}")
                StatItem("发送数据块", "${uiState.chunksSent}")
                StatItem("上传图片", "${uiState.imagesUploaded}")
                StatItem("播放音频块", "${uiState.audioChunksPlayed}")
            }

            Spacer(Modifier.height(8.dp))

            if (uiState.sessionId.isNotEmpty()) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text("Session ID:", color = TextSecondary, fontSize = 11.sp)
                    Text(uiState.sessionId, color = AccentBlue, fontSize = 11.sp, fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace)
                }
            }
        }
        }

        if (uiState.showPeerVideoToast) {
            Snackbar(
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(16.dp),
                action = {
                    TextButton(onClick = viewModel::dismissPeerVideoToast) {
                        Text("确定", color = AccentBlue)
                    }
                },
                containerColor = if (uiState.peerVideoToastMessage.contains("已连接")) {
                    AccentGreen.copy(alpha = 0.9f)
                } else {
                    AccentYellow.copy(alpha = 0.9f)
                }
            ) {
                Text(uiState.peerVideoToastMessage, color = Color.White)
            }
        }
    }
}

@Composable
fun StatusIndicator(label: String, isActive: Boolean, detail: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Box(
            modifier = Modifier
                .size(40.dp)
                .background(
                    color = if (isActive) AccentGreen.copy(alpha = 0.2f) else BorderColor,
                    shape = RoundedCornerShape(20.dp)
                ),
            contentAlignment = Alignment.Center
        ) {
            Icon(
                imageVector = if (isActive) Icons.Default.Check else Icons.Default.Close,
                contentDescription = null,
                tint = if (isActive) AccentGreen else TextSecondary,
                modifier = Modifier.size(20.dp)
            )
        }
        Spacer(Modifier.height(4.dp))
        Text(label, color = TextSecondary, fontSize = 10.sp)
        Text(detail, color = if (isActive) AccentGreen else TextSecondary, fontSize = 9.sp)
    }
}

@Composable
fun StatItem(label: String, value: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(value, color = TextPrimary, fontSize = 18.sp, fontWeight = FontWeight.Bold)
        Text(label, color = TextSecondary, fontSize = 10.sp)
    }
}

@Composable
fun darkTextFieldColors() = OutlinedTextFieldDefaults.colors(
    focusedTextColor = TextPrimary,
    unfocusedTextColor = TextPrimary,
    cursorColor = AccentBlue,
    focusedBorderColor = AccentBlue,
    unfocusedBorderColor = BorderColor,
    focusedLabelColor = AccentBlue,
    unfocusedLabelColor = TextSecondary,
    focusedLeadingIconColor = AccentBlue,
    unfocusedLeadingIconColor = TextSecondary
)