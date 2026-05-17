package com.audiochat.phone.ui

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.audiochat.phone.vision.VisionModels

@Composable
fun CameraDebugScreen(viewModel: MainViewModel, uiState: PhoneUiState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        // 摄像头状态
        DebugCard {
            SectionTitle("摄像头状态")
            Spacer(Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column {
                    Text("状态", color = TextSecondary, fontSize = 11.sp)
                    Text(
                        if (uiState.isCameraActive) "就绪" else "未初始化",
                        color = if (uiState.isCameraActive) AccentGreen else TextSecondary,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold
                    )
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text("格式", color = TextSecondary, fontSize = 11.sp)
                    Text("JPEG", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text("质量", color = TextSecondary, fontSize = 11.sp)
                    Text("${uiState.jpegQuality}%", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                }
            }
        }

        // 图片上传测试
        DebugCard {
            SectionTitle("图片上传测试")
            Spacer(Modifier.height(8.dp))

            val photoPickerLauncher = rememberLauncherForActivityResult(
                contract = ActivityResultContracts.PickVisualMedia()
            ) { uri: Uri? ->
                uri?.let {
                    viewModel.processSelectedImage(it)
                }
            }

            Button(
                onClick = { photoPickerLauncher.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) },
                enabled = uiState.isRegistered,
                colors = ButtonDefaults.buttonColors(containerColor = AccentBlue),
                modifier = Modifier.fillMaxWidth()
            ) {
                Icon(Icons.Default.Photo, null, Modifier.size(18.dp))
                Spacer(Modifier.width(6.dp))
                Text("选择图片并测试YOLO")
            }

            // 图片预览
            uiState.selectedImageUri?.let { uri ->
                Spacer(Modifier.height(8.dp))
                Text("原图", color = TextSecondary, fontSize = 10.sp)
                Spacer(Modifier.height(4.dp))
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(160.dp)
                        .clip(RoundedCornerShape(8.dp))
                        .border(1.dp, BorderColor.copy(alpha = 0.5f), RoundedCornerShape(8.dp))
                        .background(DarkSurface)
                ) {
                    AsyncImage(
                        model = uri,
                        contentDescription = "选择的图片",
                        modifier = Modifier.fillMaxSize(),
                        contentScale = ContentScale.Fit
                    )
                }
            }

            // 标注结果图
            uiState.annotatedImageUri?.let { uri ->
                Spacer(Modifier.height(8.dp))
                Text("检测结果", color = TextSecondary, fontSize = 10.sp)
                Spacer(Modifier.height(4.dp))
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(280.dp)
                        .clip(RoundedCornerShape(8.dp))
                        .border(2.dp, AccentGreen.copy(alpha = 0.7f), RoundedCornerShape(8.dp))
                        .background(DarkSurface)
                ) {
                    AsyncImage(
                        model = uri,
                        contentDescription = "检测结果",
                        modifier = Modifier.fillMaxSize(),
                        contentScale = ContentScale.Fit
                    )
                }
            }

            if (uiState.lastImageProcessResult.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                Text(
                    uiState.lastImageProcessResult,
                    color = if (uiState.lastImageProcessResult.contains("成功")) AccentGreen else AccentRed,
                    fontSize = 12.sp
                )
            }

            // 检测结果展示
            if (uiState.currentDetections.isNotEmpty()) {
                Spacer(Modifier.height(12.dp))
                Divider(color = BorderColor.copy(alpha = 0.3f))
                Spacer(Modifier.height(12.dp))

                SectionTitle("检测结果", fontSize = 12.sp)
                Spacer(Modifier.height(4.dp))

                uiState.currentDetections.take(5).forEach { detection ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(BorderColor.copy(alpha = 0.2f), RoundedCornerShape(4.dp))
                            .padding(8.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(detection.chineseName, color = TextPrimary, fontSize = 12.sp)
                        Text(
                            "${(detection.confidence * 100).toInt()}%",
                            color = AccentGreen,
                            fontSize = 12.sp,
                            fontWeight = FontWeight.Medium
                        )
                    }
                    Spacer(Modifier.height(4.dp))
                }
            }
        }

        // 图片压缩设置
        DebugCard {
            SectionTitle("图片压缩设置")
            Spacer(Modifier.height(8.dp))

            var maxSizeKB by remember { mutableStateOf("180") }
            var quality by remember { mutableStateOf("90") }
            var maxWidth by remember { mutableStateOf("960") }

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = maxSizeKB,
                    onValueChange = { maxSizeKB = it },
                    label = { Text("最大KB", fontSize = 10.sp) },
                    singleLine = true,
                    colors = darkTextFieldColors(),
                    modifier = Modifier.weight(1f)
                )
                OutlinedTextField(
                    value = quality,
                    onValueChange = { quality = it },
                    label = { Text("JPEG质量", fontSize = 10.sp) },
                    singleLine = true,
                    colors = darkTextFieldColors(),
                    modifier = Modifier.weight(1f)
                )
                OutlinedTextField(
                    value = maxWidth,
                    onValueChange = { maxWidth = it },
                    label = { Text("最大宽度", fontSize = 10.sp) },
                    singleLine = true,
                    colors = darkTextFieldColors(),
                    modifier = Modifier.weight(1f)
                )
            }

            Spacer(Modifier.height(8.dp))

            OutlinedButton(
                onClick = { viewModel.updateImageParams(maxSizeKB, quality, maxWidth) },
                colors = ButtonDefaults.outlinedButtonColors(contentColor = AccentBlue),
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("应用设置", fontSize = 12.sp)
            }
        }

        // YOLO 视觉检测状态
        DebugCard {
            SectionTitle("视觉检测 (Vision)")
            Spacer(Modifier.height(8.dp))

            // 模型选择器
            var expanded by remember { mutableStateOf(false) }
            val currentModel = VisionModels.findByName(uiState.yoloModelName) ?: VisionModels.DEFAULT_MODEL

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text("模型", color = TextSecondary, fontSize = 11.sp)
                    Box {
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable { expanded = true }
                                .background(BorderColor.copy(alpha = 0.3f), RoundedCornerShape(6.dp))
                                .padding(horizontal = 12.dp, vertical = 8.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                currentModel.displayName,
                                color = TextPrimary,
                                fontSize = 14.sp,
                                fontWeight = FontWeight.Bold
                            )
                            Icon(Icons.Default.ArrowDropDown, null, tint = TextSecondary, modifier = Modifier.size(20.dp))
                        }

                        DropdownMenu(
                            expanded = expanded,
                            onDismissRequest = { expanded = false },
                            modifier = Modifier.background(DarkSurface)
                        ) {
                            VisionModels.NCNN_MODELS.forEach { model ->
                                DropdownMenuItem(
                                    text = {
                                        Column {
                                            Text(model.displayName, color = TextPrimary, fontSize = 13.sp)
                                            Text(model.description, color = TextSecondary, fontSize = 10.sp)
                                        }
                                    },
                                    onClick = {
                                        expanded = false
                                        // TODO: 切换模型后重新加载
                                        viewModel.switchVisionModel(model.name)
                                    },
                                    leadingIcon = {
                                        if (model.name == currentModel.name) {
                                            Icon(Icons.Default.Check, null, tint = AccentGreen)
                                        }
                                    }
                                )
                            }
                        }
                    }
                }

                Spacer(Modifier.width(12.dp))

                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("状态", color = TextSecondary, fontSize = 11.sp)
                    Text(
                        if (uiState.yoloModelLoaded) "已加载" else "未加载",
                        color = if (uiState.yoloModelLoaded) AccentGreen else AccentRed,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold
                    )
                }

                Spacer(Modifier.width(12.dp))

                Column(horizontalAlignment = Alignment.End) {
                    Text("推理", color = TextSecondary, fontSize = 11.sp)
                    Text(
                        "${uiState.yoloInferenceTimeMs}ms",
                        color = if (uiState.yoloInferenceTimeMs > 0) AccentBlue else TextSecondary,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold
                    )
                }
            }

            Spacer(Modifier.height(12.dp))
            Divider(color = BorderColor.copy(alpha = 0.3f))
            Spacer(Modifier.height(12.dp))

            SectionTitle("检测统计", fontSize = 12.sp)
            Spacer(Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly
            ) {
                StatItem("处理帧数", "${uiState.framesProcessed}")
                StatItem("检测到物体", "${uiState.currentDetections.size}")
                StatItem("识别成功", "${uiState.objectsFound}")
            }

            if (uiState.currentDetections.isNotEmpty()) {
                Spacer(Modifier.height(12.dp))
                SectionTitle("当前检测结果", fontSize = 12.sp)
                Spacer(Modifier.height(4.dp))

                uiState.currentDetections.take(5).forEach { detection ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(BorderColor.copy(alpha = 0.2f), RoundedCornerShape(4.dp))
                            .padding(8.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(detection.chineseName, color = TextPrimary, fontSize = 12.sp)
                        Text(
                            "${(detection.confidence * 100).toInt()}%",
                            color = AccentGreen,
                            fontSize = 12.sp,
                            fontWeight = FontWeight.Medium
                        )
                    }
                    Spacer(Modifier.height(4.dp))
                }
            }
        }
    }
}