package com.liamxie.collector

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.text.format.Formatter
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import com.liamxie.collector.ui.theme.CollectorTheme
import java.io.File

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState); enableEdgeToEdge()
        setContent { CollectorTheme { FileManager(this) } }
    }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
private fun FileManager(activity: ComponentActivity) {
    val root = remember { File(activity.filesDir, "collector/raw").apply { mkdirs() } }
    var current by remember { mutableStateOf(root) }
    var path by remember { mutableStateOf(listOf(root)) }
    var selected by remember { mutableStateOf(setOf<String>()) }
    var revision by remember { mutableIntStateOf(0) }
    var confirmDelete by remember { mutableStateOf(false) }
    val entries = remember(current, revision) { current.listFiles()?.sortedWith(compareByDescending<File> { it.isDirectory }.thenBy { it.name.lowercase() }).orEmpty() }
    val selecting = selected.isNotEmpty()
    fun back() { if (selecting) selected = emptySet() else if (path.size > 1) { path = path.dropLast(1); current = path.last() } }
    BackHandler(enabled = selecting || path.size > 1, onBack = ::back)

    Scaffold(topBar = { TopAppBar(
        title = { Column { Text(if (selecting) "已选择 ${selected.size} 项" else if (current == root) "Collector 文件" else current.name); if (!selecting && path.size > 1) Text(path.drop(1).joinToString(" / ") { it.name }, style = MaterialTheme.typography.labelSmall, maxLines = 1) } },
        navigationIcon = { if (selecting || path.size > 1) TextButton(onClick = ::back) { Text(if (selecting) "取消" else "←") } },
        actions = {
            if (selecting) { TextButton(onClick = { selected = entries.map { it.absolutePath }.toSet() }) { Text("全选") }; TextButton(onClick = { confirmDelete = true }) { Text("删除", color = MaterialTheme.colorScheme.error) } }
            else TextButton(onClick = { revision++ }) { Text("刷新") }
        }
    ) }, floatingActionButton = {
        if (!selecting) FloatingActionButton(onClick = { activity.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }) { Text("设置", Modifier.padding(horizontal = 16.dp)) }
    }) { padding ->
        LazyColumn(Modifier.fillMaxSize().padding(padding), contentPadding = PaddingValues(bottom = 88.dp)) {
            if (entries.isEmpty()) item { Text("暂无采集文件", Modifier.padding(32.dp), color = MaterialTheme.colorScheme.onSurfaceVariant) }
            items(entries, key = { it.absolutePath }) { file ->
                val checked = file.absolutePath in selected
                ListItem(
                    headlineContent = { Text(file.name, maxLines = 1, overflow = TextOverflow.Ellipsis, fontWeight = if (file.isDirectory) FontWeight.SemiBold else FontWeight.Normal) },
                    supportingContent = { Text(if (file.isDirectory) "文件夹 · ${file.listFiles()?.size ?: 0} 项" else "${file.extension.uppercase().ifBlank { "文件" }} · ${Formatter.formatShortFileSize(activity, file.length())}") },
                    leadingContent = { Text(if (file.isDirectory) "📁" else when (file.extension.lowercase()) { "jpg", "jpeg", "png" -> "🖼️"; "json" -> "📄"; else -> "📃" }) },
                    trailingContent = { if (selecting) Checkbox(checked, onCheckedChange = { selected = toggle(selected, file.absolutePath) }) },
                    modifier = Modifier.combinedClickable(
                        onClick = { if (selecting) selected = toggle(selected, file.absolutePath) else if (file.isDirectory) { current = file; path = path + file } else openFile(activity, file) },
                        onLongClick = { selected = toggle(selected, file.absolutePath) }
                    ), colors = ListItemDefaults.colors(containerColor = if (checked) MaterialTheme.colorScheme.secondaryContainer else MaterialTheme.colorScheme.surface)
                ); HorizontalDivider()
            }
        }
    }
    if (confirmDelete) AlertDialog(onDismissRequest = { confirmDelete = false }, title = { Text("删除 ${selected.size} 项？") }, text = { Text("删除后无法恢复。") },
        confirmButton = { TextButton(onClick = { val targets = entries.filter { it.absolutePath in selected }; val failed = targets.count { !it.deleteRecursively() }; selected = emptySet(); confirmDelete = false; revision++; Toast.makeText(activity, if (failed == 0) "已删除 ${targets.size} 项" else "$failed 项删除失败", Toast.LENGTH_SHORT).show() }) { Text("删除", color = MaterialTheme.colorScheme.error) } },
        dismissButton = { TextButton(onClick = { confirmDelete = false }) { Text("取消") } })
}

private fun toggle(values: Set<String>, value: String) = if (value in values) values - value else values + value
private fun openFile(activity: ComponentActivity, file: File) {
    val mime = when (file.extension.lowercase()) { "jpg", "jpeg" -> "image/jpeg"; "png" -> "image/png"; "json" -> "application/json"; else -> "text/plain" }
    runCatching { val uri = FileProvider.getUriForFile(activity, "${activity.packageName}.files", file); activity.startActivity(Intent(Intent.ACTION_VIEW).apply { setDataAndType(uri, mime); addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION) }) }
        .onFailure { Toast.makeText(activity, "没有可打开此文件的应用", Toast.LENGTH_SHORT).show() }
}
