package com.audiochat.phone.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
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

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LogScreen(viewModel: MainViewModel, uiState: PhoneUiState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        // 日志控制
        DebugCard {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text("日志 (${uiState.logEntries.size})", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)

                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    IconButton(
                        onClick = viewModel::clearLogs,
                        modifier = Modifier.size(32.dp)
                    ) {
                        Icon(Icons.Default.Delete, null, tint = AccentRed, modifier = Modifier.size(18.dp))
                    }

                    var autoScroll by remember { mutableStateOf(true) }
                    IconButton(
                        onClick = { autoScroll = !autoScroll },
                        modifier = Modifier.size(32.dp)
                    ) {
                        Icon(
                            if (autoScroll) Icons.Default.KeyboardArrowDown else Icons.Default.Stop,
                            null,
                            tint = if (autoScroll) AccentGreen else TextSecondary,
                            modifier = Modifier.size(18.dp)
                        )
                    }
                }
            }

            Spacer(Modifier.height(4.dp))

            // 日志级别过滤
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                val logLevels = listOf(
                    "ALL" to TextPrimary,
                    "DEBUG" to TextSecondary,
                    "INFO" to AccentBlue,
                    "WARN" to AccentYellow,
                    "ERROR" to AccentRed
                )

                logLevels.forEach { (level, color) ->
                    FilterChip(
                        selected = uiState.logLevelFilter == level,
                        onClick = { viewModel.setLogLevelFilter(level) },
                        label = { Text(level, fontSize = 10.sp) },
                        colors = FilterChipDefaults.filterChipColors(
                            selectedContainerColor = color,
                            selectedLabelColor = Color.White
                        ),
                        modifier = Modifier.height(28.dp)
                    )
                }
            }
        }

        // 日志列表
        DebugCard(modifier = Modifier.weight(1f)) {
            if (uiState.filteredLogEntries.isEmpty()) {
                Box(
                    modifier = Modifier.fillMaxWidth().padding(24.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Text("暂无日志", color = TextSecondary, fontSize = 14.sp)
                }
            } else {
                val listState = rememberLazyListState()

                LaunchedEffect(uiState.filteredLogEntries.size) {
                    if (uiState.filteredLogEntries.isNotEmpty()) {
                        listState.animateScrollToItem(uiState.filteredLogEntries.size - 1)
                    }
                }

                LazyColumn(
                    state = listState,
                    verticalArrangement = Arrangement.spacedBy(2.dp)
                ) {
                    items(uiState.filteredLogEntries) { entry ->
                        LogEntryItem(entry)
                    }
                }
            }
        }
    }
}

@Composable
fun LogEntryItem(entry: LogEntry) {
    val levelColor = when (entry.level) {
        "ERROR" -> AccentRed
        "WARN" -> AccentYellow
        "INFO" -> AccentBlue
        "DEBUG" -> TextSecondary
        else -> TextSecondary
    }

    val levelBg = when (entry.level) {
        "ERROR" -> AccentRed.copy(alpha = 0.15f)
        "WARN" -> AccentYellow.copy(alpha = 0.15f)
        "INFO" -> AccentBlue.copy(alpha = 0.1f)
        else -> Color.Transparent
    }

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(levelBg, RoundedCornerShape(4.dp))
            .padding(horizontal = 6.dp, vertical = 3.dp)
    ) {
        Text(
            entry.timestamp,
            color = TextSecondary,
            fontSize = 9.sp,
            fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace
        )
        Spacer(Modifier.width(6.dp))
        Text(
            entry.level,
            color = levelColor,
            fontSize = 9.sp,
            fontWeight = FontWeight.Bold,
            fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace
        )
        Spacer(Modifier.width(6.dp))
        Text(
            entry.message,
            color = TextPrimary,
            fontSize = 10.sp,
            fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace
        )
    }
}