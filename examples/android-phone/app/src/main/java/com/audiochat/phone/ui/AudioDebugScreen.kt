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
fun AudioDebugScreen(viewModel: MainViewModel, uiState: PhoneUiState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        // 音频采集
        DebugCard {
            SectionTitle("音频采集 (sensor.mic)")
            Spacer(Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column {
                    Text("采样率", color = TextSecondary, fontSize = 11.sp)
                    Text("16000 Hz", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text("格式", color = TextSecondary, fontSize = 11.sp)
                    Text("PCM 16-bit Mono", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text("帧大小", color = TextSecondary, fontSize = 11.sp)
                    Text("640 bytes (20ms)", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                }
            }

            Spacer(Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text("状态:", color = TextSecondary, fontSize = 12.sp)
                Text(
                    if (uiState.isCapturingAudio) "采集中" else "空闲",
                    color = if (uiState.isCapturingAudio) AccentGreen else TextSecondary,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold
                )
            }

            if (uiState.isCapturingAudio) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text("已发送帧:", color = TextSecondary, fontSize = 12.sp)
                    Text("${uiState.audioFramesSent}", color = TextPrimary, fontSize = 12.sp)
                }
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text("已发送字节:", color = TextSecondary, fontSize = 12.sp)
                    Text("${uiState.audioBytesSent}", color = TextPrimary, fontSize = 12.sp)
                }
            }

            Spacer(Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Button(
                    onClick = viewModel::startAudioCapture,
                    enabled = uiState.isRegistered && !uiState.isCapturingAudio,
                    colors = ButtonDefaults.buttonColors(containerColor = AccentGreen),
                    modifier = Modifier.weight(1f)
                ) {
                    Icon(Icons.Default.Mic, null, Modifier.size(16.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("开始采集", fontSize = 12.sp)
                }

                Button(
                    onClick = viewModel::stopAudioCapture,
                    enabled = uiState.isCapturingAudio,
                    colors = ButtonDefaults.buttonColors(containerColor = AccentRed),
                    modifier = Modifier.weight(1f)
                ) {
                    Icon(Icons.Default.MicOff, null, Modifier.size(16.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("停止采集", fontSize = 12.sp)
                }
            }
        }

        // 音频播放
        DebugCard {
            SectionTitle("音频播放 (actuator.speaker)")
            Spacer(Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column {
                    Text("采样率", color = TextSecondary, fontSize = 11.sp)
                    Text("24000 Hz", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text("格式", color = TextSecondary, fontSize = 11.sp)
                    Text("PCM 16-bit Mono", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text("缓冲队列", color = TextSecondary, fontSize = 11.sp)
                    Text("${uiState.playbackQueueSize}", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                }
            }

            Spacer(Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text("状态:", color = TextSecondary, fontSize = 12.sp)
                Text(
                    if (uiState.isSpeakerActive) "播放中" else "空闲",
                    color = if (uiState.isSpeakerActive) AccentGreen else TextSecondary,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold
                )
            }

            if (uiState.isSpeakerActive) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text("已播放块:", color = TextSecondary, fontSize = 12.sp)
                    Text("${uiState.audioChunksPlayed}", color = TextPrimary, fontSize = 12.sp)
                }
            }

            Spacer(Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Button(
                    onClick = viewModel::startAudioPlayback,
                    enabled = uiState.isRegistered && !uiState.isSpeakerActive,
                    colors = ButtonDefaults.buttonColors(containerColor = AccentGreen),
                    modifier = Modifier.weight(1f)
                ) {
                    Icon(Icons.Default.PlayArrow, null, Modifier.size(16.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("开始播放", fontSize = 12.sp)
                }

                Button(
                    onClick = viewModel::stopAudioPlayback,
                    enabled = uiState.isSpeakerActive,
                    colors = ButtonDefaults.buttonColors(containerColor = AccentRed),
                    modifier = Modifier.weight(1f)
                ) {
                    Icon(Icons.Default.Stop, null, Modifier.size(16.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("停止播放", fontSize = 12.sp)
                }
            }
        }

        // 音频参数配置
        DebugCard {
            SectionTitle("音频参数")
            Spacer(Modifier.height(8.dp))

            var micSampleRate by remember { mutableStateOf("16000") }
            var speakerSampleRate by remember { mutableStateOf("24000") }
            var chunkMs by remember { mutableStateOf("20") }

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = micSampleRate,
                    onValueChange = { micSampleRate = it },
                    label = { Text("Mic采样率", fontSize = 10.sp) },
                    singleLine = true,
                    colors = darkTextFieldColors(),
                    modifier = Modifier.weight(1f)
                )
                OutlinedTextField(
                    value = speakerSampleRate,
                    onValueChange = { speakerSampleRate = it },
                    label = { Text("Speaker采样率", fontSize = 10.sp) },
                    singleLine = true,
                    colors = darkTextFieldColors(),
                    modifier = Modifier.weight(1f)
                )
                OutlinedTextField(
                    value = chunkMs,
                    onValueChange = { chunkMs = it },
                    label = { Text("帧长(ms)", fontSize = 10.sp) },
                    singleLine = true,
                    colors = darkTextFieldColors(),
                    modifier = Modifier.weight(1f)
                )
            }

            Spacer(Modifier.height(8.dp))

            OutlinedButton(
                onClick = { viewModel.updateAudioParams(micSampleRate, speakerSampleRate, chunkMs) },
                colors = ButtonDefaults.outlinedButtonColors(contentColor = AccentBlue),
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("应用参数", fontSize = 12.sp)
            }
        }
    }
}