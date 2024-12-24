from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from typing import List, Dict, Optional
import yt_dlp
import asyncio
import uuid
import os
import re
from pathlib import Path
from urllib.parse import quote
import json
from datetime import datetime
import aiohttp
import zipfile
import tempfile
import shutil

# 修改路径配置
BASE_DIR = Path("/tmp")  # 使用 /tmp 目录
STATIC_DIR = BASE_DIR / "static"
VIDEOS_DIR = STATIC_DIR / "videos"
DOWNLOADS_DIR = STATIC_DIR / "downloads"

# 确保目录存在
STATIC_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

# 修改 FastAPI 应用配置
app = FastAPI()
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# 添加健康检查路由
@app.get("/health")
async def health_check():
    return {"status": "ok"}

# 修改元数据文件路径
METADATA_FILE = BASE_DIR / "videos_metadata.json"

# 确保元数据文件存在
if not METADATA_FILE.exists():
    with open(METADATA_FILE, "w") as f:
        json.dump({}, f)

# WebSocket连接管理
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                print(f"广播错误: {str(e)}")
                continue

manager = ConnectionManager()

# 确保这个由在其他路由之前定义
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    print("WebSocket连接请求")
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        print(f"WebSocket错误: {str(e)}")
        manager.disconnect(websocket)

# 视频信息缓存
VIDEO_INFO_CACHE = {}

# 初始化模板系统
templates = Jinja2Templates(directory="templates")

# 自定义过滤器
def format_duration(seconds):
    if not seconds:
        return '未知'
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    else:
        return f"{minutes}:{seconds:02d}"

# 添加自定义过滤器
templates.env.filters['format_duration'] = format_duration

# 文件名
def sanitize_filename(filename):
    return re.sub(r'[<>:"/\\|?*]', '_', filename)

# 元数据处理函数
def load_metadata():
    if os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_metadata(metadata):
    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

# 下载进度回调
def progress_hook(d):
    if d['status'] == 'downloading':
        progress = {
            'status': 'downloading',
            'downloaded_bytes': d.get('downloaded_bytes', 0),
            'total_bytes': d.get('total_bytes', 0),
            'speed': d.get('speed', 0),
            'eta': d.get('eta', 0),
        }

# YouTube���访问性检查
async def check_youtube_access() -> tuple[bool, Optional[str]]:
    test_url = "https://www.youtube.com/favicon.ico"
    timeout = aiohttp.ClientTimeout(total=30)
    
    proxies = [
        "http://127.0.0.1:7890",
        "http://127.0.0.1:1080",
        "http://127.0.0.1:10809",
    ]
    
    for proxy in proxies:
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                async with session.get(test_url, proxy=proxy) as response:
                    if response.status == 200:
                        return True, None
        except Exception as e:
            continue
    
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.get(test_url) as response:
                if response.status == 200:
                    return True, None
    except Exception as e:
        pass
    
    return False, "无法连接到YouTube，请检查网络设置"

# 路由定义
@app.get("/")
async def read_root(request: Request):
    videos = []
    metadata = load_metadata()
    for video_id, video_data in metadata.items():
        videos.append(video_data)
    return templates.TemplateResponse("index.html", {"request": request, "videos": videos})

@app.post("/video-info")
async def get_video_info(request: Request):
    try:
        # 检查是否是批量请求
        content_type = request.headers.get('content-type', '')
        if 'application/json' in content_type:
            # 批量处理
            data = await request.json()
            urls = data.get('urls', [])
            if not urls:
                raise HTTPException(status_code=400, detail="请提供视频链接")
            
            success_count = 0
            for url in urls:
                try:
                    # 处理单个视频
                    ydl_opts = {
                        'quiet': True,
                        'no_warnings': True,
                        'extract_flat': True
                    }
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        video_info = ydl.extract_info(url, download=False)
                        if video_info:
                            # 处理视频信息并通过WebSocket发送更新
                            await process_video_info(video_info)
                            success_count += 1
                except Exception as e:
                    print(f"处理视频时出错: {str(e)}")
                    continue
            
            return {"success": True, "processed": success_count}
        else:
            # 单个视频处理
            form = await request.form()
            url = form.get("url")
            if not url:
                raise HTTPException(status_code=400, detail="请输入视频链接")
            
            # 原有的单个视频处理逻辑
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                video_info = ydl.extract_info(url, download=False)
                if video_info:
                    await process_video_info(video_info)
                    return {"success": True}
                
            raise HTTPException(status_code=400, detail="无法获取视频信息")
            
    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"处理请求时出错: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

async def process_video_info(video_info):
    # 处理视频信息并通过WebSocket发送更新
    video_data = {
        "id": video_info['id'],
        "url": video_info['webpage_url'],
        "title": video_info['title'],
        "duration": video_info.get('duration'),
        "uploader": video_info.get('uploader', 'Unknown'),
        "thumbnail": video_info.get('thumbnail'),
        "formats": extract_formats(video_info),
        "status": "pending"
    }
    
    # 保存到元数据
    metadata = load_metadata()
    metadata[video_info['id']] = video_data
    save_metadata(metadata)
    
    # 通过WebSocket发送更新
    await manager.broadcast({
        "type": "new_video",
        "video": video_data
    })

@app.post("/download/{video_id}")
async def download_video(video_id: str, request: Request):
    try:
        form = await request.form()
        format_id = form.get("format_id", "best")
        
        # 加载元数据
        metadata = load_metadata()
        video_info = metadata.get(video_id)
        if not video_info:
            raise HTTPException(status_code=404, detail="视频信息不存在")
        
        # 设置下载选项
        filename = f"{sanitize_filename(video_info['title'])}.%(ext)s"
        download_path = str(VIDEOS_DIR / filename)
        
        ydl_opts = {
            'format': format_id,
            'outtmpl': download_path,
            'progress_hooks': [progress_hook],
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_info['url']])
                
            # 获取实际下载的文件路径
            actual_file = next(VIDEOS_DIR.glob(sanitize_filename(video_info['title']) + ".*"))
            relative_path = actual_file.relative_to(STATIC_DIR)
            
            # 更新元数据
            video_info['status'] = 'completed'
            video_info['file_path'] = str(relative_path)
            metadata[video_id] = video_info
            save_metadata(metadata)
            
            # 发送更新
            await manager.broadcast({
                "type": "video_status",
                "video_id": video_id,
                "status": "completed",
                "file_path": str(relative_path)
            })
            
            return {"success": True}
            
        except Exception as e:
            print(f"下载视频时出错: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
            
    except Exception as e:
        print(f"处理下载请求时出错: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/check-youtube")
async def check_youtube():
    can_access, error_msg = await check_youtube_access()
    return {
        "accessible": can_access,
        "message": "YouTube可以常问" if can_access else error_msg
    }

@app.get("/videos")
async def get_videos():
    return load_metadata()

@app.delete("/videos")
async def delete_all_videos():
    try:
        for file in VIDEOS_DIR.glob("*"):
            try:
                file.unlink()
            except Exception as e:
                print(f"删除文件 {file} 失败: {e}")
        
        if os.path.exists(METADATA_FILE):
            os.remove(METADATA_FILE)
        
        return {"success": True, "message": "所有视频已清空"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清空视频失败: {str(e)}")

@app.post("/update-format/{video_id}")
async def update_format(video_id: str, format_data: dict):
    try:
        video_info = VIDEO_INFO_CACHE.get(video_id)
        if not video_info:
            raise HTTPException(status_code=404, detail="视频信息已过期")
        
        format_id = format_data.get('format_id')
        if not format_id:
            raise HTTPException(status_code=400, detail="未指定格式ID")
        
        # 更新选中的格式
        selected_format = next(
            (f for f in video_info['formats'] if f['format_id'] == format_id),
            None
        )
        
        if not selected_format:
            raise HTTPException(status_code=400, detail="无效的格式ID")
        
        video_info['selected_format'] = selected_format
        
        # 更新metadata
        all_metadata = load_metadata()
        if video_id in all_metadata:
            all_metadata[video_id]['selected_format'] = selected_format
            save_metadata(all_metadata)
        
        return {"success": True}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/batch-info")
async def process_batch_videos(request: Request):
    try:
        data = await request.json()
        urls = data.get("urls", [])
        
        if not urls:
            raise HTTPException(status_code=400, detail="请提供视频链接")
        
        success_count = 0
        for url in urls:
            try:
                # 使用现有的视频处理逻辑
                video_info = await get_video_info(url)
                if video_info:
                    success_count += 1
                    # 发送WebSocket更新
                    await manager.broadcast({
                        "type": "new_video",
                        "video": video_info
                    })
            except Exception as e:
                print(f"处理视频时出错: {str(e)}")
                continue
        
        return {"success": True, "processed": success_count}
        
    except Exception as e:
        print(f"批量处理时出错: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/channel-info")
async def get_channel_info(request: Request):
    try:
        form = await request.form()
        channel_url = form.get("channel_url")
        if not channel_url:
            raise HTTPException(status_code=400, detail="请输入用户/频道链接")
        
        # 检查并规范化频道URL
        if not any(x in channel_url for x in ['youtube.com/channel/', 'youtube.com/c/', 'youtube.com/user/', 'youtube.com/@']):
            if channel_url.startswith('@'):
                channel_url = f"https://www.youtube.com/{channel_url}"
            else:
                raise HTTPException(status_code=400, detail="请输入有效的YouTube频道/用户链接")
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': 'in_playlist',
            'playlistend': 50,
            'ignoreerrors': True,
            'extract_flat': True,
            'dump_single_json': True,
            'force_generic_extractor': False,
            'no_color': True,
        }
        
        print(f"正在获取频道信息: {channel_url}")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                # 先获取频道主页
                channel_info = await asyncio.to_thread(ydl.extract_info, channel_url, download=False)
                if not channel_info:
                    raise HTTPException(status_code=400, detail="无法获取频道信息")
                
                # 获取上传的视频列表
                if 'entries' not in channel_info:
                    # 尝试获取上传视频列表
                    channel_id = channel_info.get('channel_id')
                    if channel_id:
                        uploads_url = f"https://www.youtube.com/channel/{channel_id}/videos"
                        channel_info = await asyncio.to_thread(ydl.extract_info, uploads_url, download=False)
                
                entries = channel_info.get('entries', [])
                if not entries:
                    raise HTTPException(status_code=400, detail="未找到任何视频")
                
                print(f"找到 {len(entries)} 个视频")
                
                success_count = 0
                for entry in entries:
                    try:
                        if not entry or not entry.get('id'):
                            continue
                            
                        video_id = str(uuid.uuid4())
                        video_url = f"https://www.youtube.com/watch?v={entry['id']}"
                        
                        print(f"处理视频: {video_url}")
                        
                        # ���取详细视频信息
                        video_opts = {
                            'quiet': True,
                            'no_warnings': True,
                            'format': 'best',
                        }
                        
                        with yt_dlp.YoutubeDL(video_opts) as video_ydl:
                            video_info = await asyncio.to_thread(video_ydl.extract_info, video_url, download=False)
                            if not video_info:
                                continue
                                
                            formats = []
                            for f in video_info.get('formats', []):
                                if f.get('vcodec') != 'none':
                                    resolution = f.get('resolution', 'unknown')
                                    formats.append({
                                        'format_id': f.get('format_id'),
                                        'ext': f.get('ext'),
                                        'resolution': resolution,
                                        'filesize': f.get('filesize', 0),
                                        'format_note': f.get('format_note', ''),
                                        'vcodec': f.get('vcodec'),
                                        'acodec': f.get('acodec')
                                    })
                            
                            if not formats:
                                continue
                                
                            formats.sort(key=lambda x: (
                                int(x['resolution'].split('x')[0]) if 'x' in x['resolution'] else 0
                            ), reverse=True)
                            
                            video_data = {
                                'id': video_id,
                                'url': video_url,
                                'title': video_info.get('title', 'Unknown Title'),
                                'duration': video_info.get('duration'),
                                'uploader': video_info.get('uploader', 'Unknown'),
                                'thumbnail': video_info.get('thumbnail'),
                                'formats': formats,
                                'selected_format': formats[0] if formats else None,
                                'status': 'pending'
                            }
                            
                            VIDEO_INFO_CACHE[video_id] = video_data
                            
                            all_metadata = load_metadata()
                            all_metadata[video_id] = video_data
                            save_metadata(all_metadata)
                            
                            # 在成功处理每个视频后，通过WebSocket发送更新
                            await manager.broadcast({
                                "type": "new_video",
                                "video": {
                                    "id": video_id,
                                    "url": video_url,
                                    "title": video_info.get('title', 'Unknown Title'),
                                    "duration": video_info.get('duration'),
                                    "uploader": video_info.get('uploader', 'Unknown'),
                                    "thumbnail": video_info.get('thumbnail'),
                                    "formats": formats,
                                    "selected_format": formats[0] if formats else None,
                                    "status": "pending"
                                }
                            })
                            
                            success_count += 1
                            print(f"成功处理视频: {video_data['title']}")
                            
                    except Exception as e:
                        print(f"处理单个视频时出错: {str(e)}")
                        continue
                
                if success_count == 0:
                    raise HTTPException(status_code=400, detail="没有成功处理任何视频")
                
                return {"success": True, "processed": success_count}
                
            except Exception as e:
                print(f"处理频道视频时出错: {str(e)}")
                raise HTTPException(status_code=400, detail=f"获取频道视频失败: {str(e)}")
            
    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"处理请求时出错: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# 修改广播函数
async def broadcast_progress(message: dict):
    await manager.broadcast(message)

@app.post("/download-all")
async def download_all_videos():
    try:
        all_metadata = load_metadata()
        if not all_metadata:
            raise HTTPException(status_code=400, detail="没有可下载的视频")

        # 先下载所有未下载的视频
        total_videos = len(all_metadata)
        processed = 0

        for video_id, video_info in all_metadata.items():
            try:
                if video_info['status'] != 'completed':
                    # 发送下载开始通知
                    await manager.broadcast({
                        "type": "download_status",
                        "current": processed + 1,
                        "total": total_videos,
                        "title": video_info['title'],
                        "status": "downloading"
                    })

                    # 设置下载选项
                    filename = f"{sanitize_filename(video_info['title'])}.%(ext)s"
                    download_path = str(VIDEOS_DIR / filename)
                    
                    ydl_opts = {
                        'format': video_info.get('selected_format', {}).get('format_id', 'best'),
                        'outtmpl': download_path,
                        'progress_hooks': [progress_hook],
                    }
                    
                    # 下载视频
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([video_info['url']])
                    
                    # 获取实际下载的文件路径
                    actual_file = next(VIDEOS_DIR.glob(sanitize_filename(video_info['title']) + ".*"))
                    relative_path = actual_file.relative_to(STATIC_DIR)
                    
                    # 更新元数据
                    video_info['status'] = 'completed'
                    video_info['file_path'] = str(relative_path)
                    all_metadata[video_id] = video_info
                    save_metadata(all_metadata)

                processed += 1
                
            except Exception as e:
                print(f"下载视频时出错: {str(e)}")
                continue

        # 所有视频下载完成后，创建zip文件
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        zip_filename = f"youtube_videos_{timestamp}.zip"
        zip_path = DOWNLOADS_DIR / zip_filename

        # 创建zip文件
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            processed = 0
            
            for video_id, video_info in all_metadata.items():
                try:
                    if video_info['status'] == 'completed':
                        # 构建视频文件路径
                        file_path = Path(video_info['file_path'])
                        full_path = STATIC_DIR / file_path
                        
                        print(f"打包视频: {video_info['title']}")
                        print(f"文件路径: {full_path}")
                        
                        if full_path.exists():
                            # 使用视频标题作为zip内的文件名
                            safe_filename = sanitize_filename(video_info['title'])
                            safe_filename = f"{safe_filename}{full_path.suffix}"
                            
                            print(f"添加到zip: {safe_filename}")
                            
                            # 添加到zip
                            zipf.write(full_path, safe_filename)
                            
                            processed += 1
                            await manager.broadcast({
                                "type": "zip_progress",
                                "current": processed,
                                "total": total_videos,
                                "filename": safe_filename
                            })
                except Exception as e:
                    print(f"打包视频时出错: {str(e)}")
                    continue

        # 检查zip文件
        if zip_path.exists():
            zip_size = zip_path.stat().st_size
            print(f"ZIP文件大小: {zip_size} bytes")
            if zip_size == 0:
                raise HTTPException(status_code=500, detail="创建的ZIP文件为空")

        return FileResponse(
            path=zip_path,
            filename=zip_filename,
            media_type='application/zip'
        )

    except Exception as e:
        print(f"批量下载时出错: {str(e)}")
        if 'zip_path' in locals() and zip_path.exists():
            try:
                zip_path.unlink()
            except:
                pass
        raise HTTPException(status_code=500, detail=str(e))

# 添加定时清理任务（可选）
@app.on_event("startup")
async def startup_event():
    # 清理旧的zip文件
    try:
        for zip_file in DOWNLOADS_DIR.glob("*.zip"):
            if zip_file.stat().st_mtime < (datetime.now().timestamp() - 3600):  # 1小时前的文件
                try:
                    zip_file.unlink()
                except:
                    pass
    except:
        pass

def extract_formats(video_info):
    formats = []
    try:
        for f in video_info.get('formats', []):
            # 检查是否有视频流
            if f.get('vcodec', 'none') != 'none':
                format_id = f.get('format_id', '')
                ext = f.get('ext', '')
                
                # 获取分辨率
                if f.get('height'):
                    resolution = f'{f["height"]}p'
                else:
                    resolution = 'unknown'
                
                # 获取视频质量
                quality = f.get('format_note', '')
                if quality:
                    resolution = f"{resolution} ({quality})"
                
                formats.append({
                    'format_id': format_id,
                    'ext': ext,
                    'resolution': resolution
                })
    except Exception as e:
        print(f"提取格式信息时出错: {str(e)}")
    
    # 按分辨率排序（从高到低）
    formats.sort(key=lambda x: int(x['resolution'].split('p')[0]) if x['resolution'].split('p')[0].isdigit() else 0, reverse=True)
    
    return formats