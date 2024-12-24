# YouTube Video Downloader 产品设计文档

## 1. 产品概述

YouTube Video Downloader 是一个基于 FastAPI 和 yt-dlp 的 YouTube 视频下载工具,支持单个视频下载、批量下载以及频道视频批量下载功能。

## 2. 核心功能

### 2.1 视频信息获取
- 支持单个视频链接解析
- 支持批量视频链接解析
- 支持频道/用户视频批量解析
- 自动提取视频标题、时长、上传者等信息
- 自动提取可用的视频格式和分辨率

参考代码: 

python:main.py
startLine: 155
endLine: 206

### 2.2 视频下载功能
- 单个视频下载
- 批量视频下载
- 支持选择视频质量
- 实时显示下载进度
- 下载完成后自动更新状态

参考代码:
```python:main.py
startLine: 238
endLine: 267
```

### 2.3 批量打包下载
- 支持已下载视频批量打包
- 自动创建带时间戳的zip文件
- 显示打包进度
- 自动清理过期zip文件

参考代码:
```python:main.py
startLine: 542
endLine: 655
```

## 3. 技术架构

### 3.1 后端架构
- FastAPI 作为Web框架
- yt-dlp 负责视频解析和下载
- WebSocket 实现实时进度推送
- 文件系统存储下载的视频和元数据

### 3.2 前端架构
- 纯HTML + TailwindCSS构建UI
- WebSocket接收实时更新
- 支持响应式布局

### 3.3 数据存储
- 视频元数据使用JSON文件存储
- 视频文件存储在static/videos目录
- 临时zip文件存储在static/downloads目录

## 4. 关键流程

### 4.1 视频信息获取流程
1. 用户输入视频链接
2. 后端解析视频信息
3. 保存视频元数据
4. WebSocket推送更新前端

### 4.2 视频下载流程
1. 用户选择视频质量并点击下载
2. 后端开始下载视频
3. 实时推送下载进度
4. 下载完成后更新状态

### 4.3 批量下载流程
1. 用户点击"下载全部视频"
2. 后端检查未下载视频
3. 依次下载所有视频
4. 打包已下载视频
5. 返回zip文件

## 5. 目录结构
```
.
├── static/
│   ├── videos/      # 存储下载的视频
│   └── downloads/   # 存储临时zip文件
├── templates/
│   └── index.html   # 前端页面
├── main.py          # 后端主程序
└── videos_metadata.json  # 视频元数据
```

## 6. 待优化项目

### 6.1 功能优化
- 添加下载速度显示
- 添加剩余时间估计
- 添加取消下载功能
- 添加选择性打包功能
- 优化文件命名规则

### 6.2 性能优化
- 优化大文件下载性能
- 添加并发下载支持
- 优化内存使用

### 6.3 用户体验
- 添加深色模式
- 优化移动端适配
- 添加国际化支持
- 优化错误提示

## 7. 注意事项

### 7.1 系统要求
- Python 3.7+
- 足够的磁盘空间
- 稳定的网络连接
- 合适的代理设置(可选)

### 7.2 使用限制
- 需要遵守YouTube服务条款
- 仅供个人使用
- 注意视频版权问题

## 8. 更新日志

### v1.0.0
- 实现基础视频下载功能
- 支持批量下载和打包
- 添加WebSocket实时进度
- 实现基础UI界面