import pandas as pd
import os

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from youtube import is_youtubeshorts, youtube_transcript
from googleapiclient.discovery import build


# # 유튜브 동영상 정보를 저장하고 불러오는 모듈 # #
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')

# PostgreSQL에 접속
def connect_postgres():
    conn = psycopg2.connect(
        host="15.164.112.237", 
        database="dify", 
        user="difyuser", 
        password="bico0218"
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    
    return conn


# 채널 정보 저장
def save_info(table_name, search_unique_id, keyword, channel_url, channel_name, channel_subscribers, 
              video_id, video_title, video_thumbnail, video_view_count, video_like_count, video_comment_count, video_view_subscriber_ratio, 
              is_shorts, transcript, published_at, top_comments):
    conn = connect_postgres()
    cur = conn.cursor()
    
    # 댓글 정보 준비 (각 댓글을 별도 변수로)
    comment_1 = ""
    comment_2 = ""
    comment_3 = ""
    
    # 댓글 정보 준비 (내용만 저장)
    comment_1 = top_comments[0]['text'] if len(top_comments) > 0 else "내용 없음"
    comment_2 = top_comments[1]['text'] if len(top_comments) > 1 else "내용 없음"
    comment_3 = top_comments[2]['text'] if len(top_comments) > 2 else "내용 없음"
    
    cur.execute(f"""
    INSERT INTO {table_name} (
        search_unique_id, keyword, channel_url, channel_name, channel_subscribers, 
        video_id, video_title, video_thumbnail, video_view_count, video_like_count, video_comment_count, video_view_subscriber_ratio, 
        is_shorts, transcript, published_at, comment_1, comment_2, comment_3)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, 
        (search_unique_id, keyword, channel_url, channel_name, channel_subscribers, 
         video_id, video_title, video_thumbnail, video_view_count, video_like_count, video_comment_count, video_view_subscriber_ratio, 
         is_shorts, transcript, published_at, comment_1, comment_2, comment_3)
    )
    
    conn.commit()
    cur.close()
    conn.close()

# 정보 불러오기
def load_info(search_id, table_name):
    conn = connect_postgres()
    cur = conn.cursor()

    cur.execute(f"""
    SELECT 
        keyword, channel_url, channel_name, video_id, video_title, video_thumbnail, video_view_count, video_like_count, video_comment_count, video_view_subscriber_ratio, is_shorts, comment_1, comment_2, comment_3, transcript
    FROM 
        {table_name} 
    WHERE 
        search_unique_id = %s
    """, (search_id,))

    results = cur.fetchall()

    # 키워드도 조회해야
    columns = [
        '키워드', '채널URL', '채널명', 'video_id', '제목', '썸네일', '조회수', '좋아요', '댓글수', 
        '조회수/구독자 비율', '쇼츠', '댓글1', '댓글2', '댓글3', '스크립트'
    ]
    
    df = pd.DataFrame(results, columns=columns)
    
    cur.close()
    conn.close()
    
    return df

# 키워드로 동영상 정보 불러오기
def fetch_youtube_data(search_query, max_results=50):
    youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
    
    videos_data = []
    request = youtube.search().list(
        q=search_query,
        part='snippet',
        type='video',
        maxResults=min(50, max_results)
    )
    
    response = request.execute()
    video_ids = [item['id']['videoId'] for item in response['items']]
    stats_request = youtube.videos().list(
        part='statistics',
        id=','.join(video_ids)
    )
    stats_response = stats_request.execute()
    channel_ids = [item['snippet']['channelId'] for item in response['items']]
    channels_request = youtube.channels().list(
        part='statistics',
        id=','.join(set(channel_ids))
    )
    channels_response = channels_request.execute()
    
    channel_subscribers = {
        channel['id']: int(channel['statistics'].get('subscriberCount', 0))
        for channel in channels_response['items']
    }
    
    for video, stats in zip(response['items'], stats_response['items']):
        views = int(stats['statistics'].get('viewCount', 0))
        if views >= 1000:
            video_id = video['id']['videoId']
            channel_id = video['snippet']['channelId']
            subscriber_count = channel_subscribers.get(channel_id, 0)
            view_sub_ratio = (views / subscriber_count) * 100 if subscriber_count > 0 else 0
            script = youtube_transcript(video_id)
            is_shorts = is_youtubeshorts(video_id)
            videos_data.append({
                'title': video['snippet']['title'], 
                'channel': video['snippet']['channelTitle'], 
                'publishedAt': video['snippet']['publishedAt'], 
                'views': views, 
                'subscribers': subscriber_count, 
                'view_sub_ratio': round(view_sub_ratio, 2),
                'likes': int(stats['statistics'].get('likeCount', 0)), 
                'comments': int(stats['statistics'].get('commentCount', 0)), 
                'description': video['snippet']['description'], 
                'url': f"https://www.youtube.com/watch?v={video_id}", 
                'thumbnail': video['snippet']['thumbnails']['high']['url'], 
                '1min_script': script,
                'is_shorts': is_shorts
            })
    
    return pd.DataFrame(videos_data)

# 각 search_unique_id별로 가장 높은 비율의 동영상 하나씩 가져오는 함수
def get_top_videos_by_search_id(table_name):
    conn = connect_postgres()
    cur = conn.cursor()

    # 모든 고유 search_unique_id 가져오기
    cur.execute(f"SELECT DISTINCT search_unique_id FROM {table_name} ORDER BY search_unique_id DESC")
    search_ids = [row[0] for row in cur.fetchall()]
    
    # 결과 데이터를 저장할 리스트
    results = []
    
    # 각 search_unique_id에 대해 가장 높은 video_view_subscriber_ratio를 가진 동영상 가져오기
    for search_id in search_ids:
        cur.execute(f"""
        SELECT 
            video_thumbnail, search_unique_id, keyword, channel_name, video_id, video_title, 
            video_view_count, video_like_count, video_comment_count, video_view_subscriber_ratio, 
            is_shorts, comment_1, comment_2, comment_3, transcript
        FROM 
            {table_name} 
        WHERE 
            search_unique_id = %s
        ORDER BY 
            video_view_subscriber_ratio DESC
        LIMIT 1
        """, (search_id,))
        
        row = cur.fetchone()
        if row:
            results.append(row)
    
    # 컬럼 이름
    columns = [
        '썸네일', 'pk_ID', '키워드', '채널명', 'video_id', '제목', 
        '조회수', '좋아요', '댓글수', '조회수/구독자 비율', 
        '쇼츠', '댓글1', '댓글2', '댓글3', '스크립트'
    ]
    
    df = pd.DataFrame(results, columns=columns)
    
    cur.close()
    conn.close()
    
    return df

# 분석 결과 저장 함수
def save_video_analysis(table_name, search_unique_id, is_shorts, analysis_result):
    conn = connect_postgres()
    cur = conn.cursor()
    
    cur.execute(f"""
        INSERT INTO {table_name} (search_unique_id, is_shorts, llm_analysis) VALUES (%s, %s, %s)
        """,
        (search_unique_id, is_shorts, analysis_result)
    )
    
    conn.commit()
    cur.close()
    conn.close()

# 썸네일 분석 저장 함수
def save_thumbnail_analysis(thumbnail_data, search_unique_id, is_shorts, url):
    conn = connect_postgres()
    cur = conn.cursor()
    
    for item in thumbnail_data:
        cur.execute("""
            INSERT INTO thumbnail_analysis 
            (search_unique_id, keyword, channel_url, channel_name, video_id, video_title, video_thumbnail, is_shorts, thumbnail_analysis)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (search_unique_id, item['키워드'], url, item['채널명'], item['video_id'], item['제목'], item['썸네일'], is_shorts, item['분석'])
        )
    
    conn.commit()
    cur.close()
    conn.close()
