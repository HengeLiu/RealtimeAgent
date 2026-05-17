package com.audiochat.phone.ui

import android.graphics.Bitmap
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.audiochat.phone.video.PeerVideoTaskManager
import com.audiochat.phone.vision.YoloDetector

/**
 * 视频显示页面
 * 显示眼镜端传来的视频和 YOLO 检测结果
 */
@Composable
fun VideoDisplayScreen(
    currentFrame: PeerVideoTaskManager.FrameResult?,
    taskState: PeerVideoTaskManager.TaskState?,
    detections: List<YoloDetector.Detection>,
    onStopTask: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
    ) {
        TaskStatusCard(
            taskState = taskState,
            modifier = Modifier.fillMaxWidth()
        )

        Spacer(modifier = Modifier.height(16.dp))

        Box(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
                .clip(RoundedCornerShape(12.dp))
                .background(Color.Black)
        ) {
            if (currentFrame != null) {
                Image(
                    bitmap = currentFrame.annotatedBitmap.asImageBitmap(),
                    contentDescription = "Video Frame",
                    modifier = Modifier.fillMaxSize(),
                    contentScale = ContentScale.Fit
                )

                FrameInfoOverlay(
                    frameSeq = currentFrame.frameSeq,
                    inferenceTime = currentFrame.inferenceTimeMs,
                    modifier = Modifier.align(Alignment.TopEnd)
                )
            } else {
                NoVideoPlaceholder(
                    taskState = taskState,
                    modifier = Modifier.align(Alignment.Center)
                )
            }
        }

        Spacer(modifier = Modifier.height(16.dp))

        if (detections.isNotEmpty()) {
            DetectionsList(
                detections = detections,
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(max = 150.dp)
            )
        }

        if (taskState != null) {
            Spacer(modifier = Modifier.height(16.dp))
            
            Button(
                onClick = onStopTask,
                modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.error
                )
            ) {
                Icon(Icons.Default.Stop, contentDescription = null)
                Spacer(modifier = Modifier.width(8.dp))
                Text("停止任务")
            }
        }
    }
}

@Composable
private fun TaskStatusCard(
    taskState: PeerVideoTaskManager.TaskState?,
    modifier: Modifier = Modifier
) {
    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(
            containerColor = when {
                taskState == null -> MaterialTheme.colorScheme.surfaceVariant
                taskState.taskType == "find_object_task" -> MaterialTheme.colorScheme.primaryContainer
                taskState.taskType == "traffic_light_task" -> MaterialTheme.colorScheme.secondaryContainer
                else -> MaterialTheme.colorScheme.surfaceVariant
            }
        )
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Icon(
                imageVector = when {
                    taskState == null -> Icons.Default.VideocamOff
                    taskState.taskType == "find_object_task" -> Icons.Default.Search
                    taskState.taskType == "traffic_light_task" -> Icons.Default.Traffic
                    else -> Icons.Default.Videocam
                },
                contentDescription = null,
                modifier = Modifier.size(32.dp)
            )

            Spacer(modifier = Modifier.width(16.dp))

            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = when {
                        taskState == null -> "等待任务"
                        taskState.taskType == "find_object_task" -> "找物任务"
                        taskState.taskType == "traffic_light_task" -> "红绿灯识别"
                        else -> "视频任务"
                    },
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold
                )

                if (taskState != null) {
                    Text(
                        text = when (taskState.purpose) {
                            "find_object" -> "正在寻找: ${taskState.objectName}"
                            "traffic_light" -> "正在识别红绿灯"
                            else -> "任务进行中"
                        },
                        style = MaterialTheme.typography.bodyMedium
                    )
                }
            }

            if (taskState != null) {
                CircularProgressIndicator(
                    modifier = Modifier.size(24.dp),
                    strokeWidth = 2.dp
                )
            }
        }
    }
}

@Composable
private fun FrameInfoOverlay(
    frameSeq: Long,
    inferenceTime: Long,
    modifier: Modifier = Modifier
) {
    Card(
        modifier = modifier.padding(8.dp),
        colors = CardDefaults.cardColors(
            containerColor = Color.Black.copy(alpha = 0.6f)
        )
    ) {
        Column(
            modifier = Modifier.padding(8.dp)
        ) {
            Text(
                text = "帧: $frameSeq",
                color = Color.White,
                fontSize = 12.sp
            )
            Text(
                text = "推理: ${inferenceTime}ms",
                color = if (inferenceTime < 100) Color.Green else Color.Yellow,
                fontSize = 12.sp
            )
        }
    }
}

@Composable
private fun NoVideoPlaceholder(
    taskState: PeerVideoTaskManager.TaskState?,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Icon(
            imageVector = if (taskState != null) Icons.Default.Videocam else Icons.Default.VideocamOff,
            contentDescription = null,
            modifier = Modifier.size(64.dp),
            tint = Color.White.copy(alpha = 0.5f)
        )

        Spacer(modifier = Modifier.height(16.dp))

        Text(
            text = if (taskState != null) "等待视频连接..." else "暂无视频",
            color = Color.White.copy(alpha = 0.7f),
            style = MaterialTheme.typography.bodyLarge
        )

        if (taskState != null) {
            Spacer(modifier = Modifier.height(8.dp))
            CircularProgressIndicator(
                modifier = Modifier.size(24.dp),
                color = Color.White,
                strokeWidth = 2.dp
            )
        }
    }
}

@Composable
private fun DetectionsList(
    detections: List<YoloDetector.Detection>,
    modifier: Modifier = Modifier
) {
    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant
        )
    ) {
        Column(
            modifier = Modifier.padding(12.dp)
        ) {
            Text(
                text = "检测结果 (${detections.size})",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold
            )

            Spacer(modifier = Modifier.height(8.dp))

            detections.take(5).forEach { detection ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Icon(
                        imageVector = Icons.Default.CheckCircle,
                        contentDescription = null,
                        modifier = Modifier.size(16.dp),
                        tint = MaterialTheme.colorScheme.primary
                    )

                    Spacer(modifier = Modifier.width(8.dp))

                    Text(
                        text = "${detection.chineseName} (${(detection.confidence * 100).toInt()}%)",
                        style = MaterialTheme.typography.bodyMedium
                    )
                }
            }

            if (detections.size > 5) {
                Text(
                    text = "还有 ${detections.size - 5} 个...",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}
