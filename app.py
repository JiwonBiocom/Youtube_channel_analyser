import streamlit as st
from googleapiclient.discovery import build
import pandas as pd
from PIL import Image
import requests
from io import BytesIO
import os
from dotenv import load_dotenv

# 앱 설정
st.set_page_config(page_title="YouTube Channel Analyzer", layout="wide")

load_dotenv()

YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')

def is_youtubeshorts(video_id):
    url = 'https://www.youtube.com/shorts/' + video_id
    req = requests.head(url)
    
    return req.status_code == 200

class YouTubeAnalyzer:
    def __init__(self, api_key):
        self.youtube = build('youtube', 'v3', developerKey=api_key)
    
    def get_channel_id(self, channel_url):
        """Extract channel ID from URL or custom URL"""
        if 'youtube.com/channel/' in channel_url:
            return channel_url.split('channel/')[1].split('/')[0]
        elif 'youtube.com/@' in channel_url:
            username = channel_url.split('@')[1].split('/')[0]
            request = self.youtube.channels().list(
                part='id',
                forUsername=username
            )
            try:
                response = request.execute()
                if response.get('items'):
                    return response['items'][0]['id']
            except:
                pass
            
            # If the above method fails, try with search
            request = self.youtube.search().list(
                part='snippet',
                q=username,
                type='channel',
                maxResults=1
            )
            response = request.execute()
            
            # Verify the channel handle matches
            for item in response['items']:
                channel_id = item['snippet']['channelId']
                channel_info = self.youtube.channels().list(
                    part='snippet',
                    id=channel_id
                ).execute()
                
                if channel_info['items'][0]['snippet'].get('customUrl', '').lower() == f'@{username.lower()}':
                    return channel_id
            
            raise ValueError(f"Could not find channel ID for {channel_url}")
    
    def get_channel_stats(self, channel_id):
        """Get channel statistics including subscriber count"""
        request = self.youtube.channels().list(
            part='statistics,snippet',
            id=channel_id
        )
        response = request.execute()
        channel_info = response['items'][0]
        return {
            'title': channel_info['snippet']['title'],
            'subscribers': int(channel_info['statistics']['subscriberCount']),
            'thumbnail': channel_info['snippet']['thumbnails']['high']['url']
        }
    
    # 해당 채널의 동영상 정보보
    def get_all_videos(self, channel_id):
        videos = []
        next_page_token = None
        
        while True:
            request = self.youtube.search().list(
                part='snippet',
                channelId=channel_id,
                maxResults=50,
                type='video',
                pageToken=next_page_token,
                order='date'  # 최신 영상부터
            )
            response = request.execute()
            
            # Get video IDs for batch statistics request
            video_ids = [item['id']['videoId'] for item in response['items']]
            
            # Get video statistics in batch
            stats_request = self.youtube.videos().list(
                part='statistics',
                id=','.join(video_ids)
            )
            stats_response = stats_request.execute()
            
            # Combine video information with statistics
            for video, stats in zip(response['items'], stats_response['items']):
                videos.append({
                    'title': video['snippet']['title'],
                    'thumbnail': video['snippet']['thumbnails']['high']['url'],
                    'views': int(stats['statistics'].get('viewCount', 0)),
                    'like_count': int(stats['statistics'].get('likeCount', 0)),
                    'comment_count': int(stats['statistics'].get('commentCount', 0)),
                    'published_at': video['snippet']['publishedAt'],
                    'video_id': video['id']['videoId']
                })
            
            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                break
        
        return videos

def main():
    st.title("YouTube Channel Analyzer")
    
    # Input for API key
    api_key = YOUTUBE_API_KEY  # api_key = st.text_input("YouTube Data API Key", type="password")
    
    # Input for channel URL
    channel_url = st.text_input("YouTube Channel URL (e.g., https://youtube.com/@channelname)")
    
    if api_key and channel_url:
        try:
            analyzer = YouTubeAnalyzer(api_key)
            
            with st.spinner("분석 중..."):
                # Get channel info
                channel_id = analyzer.get_channel_id(channel_url)
                channel_info = analyzer.get_channel_stats(channel_id)
                
                # Display channel information
                col1, col2 = st.columns([1, 3])
                with col1:
                    response = requests.get(channel_info['thumbnail'])
                    img = Image.open(BytesIO(response.content))
                    st.image(img, width=200)
                
                with col2:
                    st.subheader(channel_info['title'])
                    st.metric("구독자 수", f"{channel_info['subscribers']:,}")
                
                # 모든 영상 정보 가져오기
                videos = analyzer.get_all_videos(channel_id)
                df = pd.DataFrame(videos)
                df['views_subscriber_ratio'] = (df['views'] / channel_info['subscribers'] * 100).round(2)
                
                # Sort by views/subscriber ratio
                df = df.sort_values('views_subscriber_ratio', ascending=False).head(10).copy()  # df = df.sort_values('views_subscriber_ratio', ascending=False).head(10)
                
                # 각 영상의 롱폼/쇼츠 여부 추가 ("쇼츠"면 쇼츠, 아니면 롱폼)
                df['롱폼/쇼츠 여부'] = df['video_id'].apply(lambda vid: "쇼츠" if is_youtubeshorts(vid) else "롱폼")

                # Display videos in a table
                st.subheader("조회수/구독자 비율 TOP 10 영상")
                
                # Create a custom dataframe for display
                display_df = df[['thumbnail', 'title', 'views', 'views_subscriber_ratio', 'like_count', 'comment_count', '롱폼/쇼츠 여부']].copy()

                def format_to_10k(n):
                    num = round(n / 10000, 1)  # 만 단위로 변환하고 소수점 첫째자리에서 반올림

                    return f"{num}만"
                
                # Format numbers
                display_df['views'] = display_df['views'].apply(format_to_10k)
                display_df['views_subscriber_ratio'] = display_df['views_subscriber_ratio'].apply(lambda x: f"{int(round(x))}%")
                
                # 썸네일 이미지를 HTML 태그로 변환
                display_df['thumbnail'] = display_df['thumbnail'].apply(lambda url: f'<img src="{url}" width="120">')
                
                # 제목을 클릭하면 해당 동영상 페이지로 이동하도록 HTML 앵커 태그 적용
                display_df['title'] = display_df.apply(
                    lambda row: f'<a href="https://www.youtube.com/watch?v={row["video_id"]}" target="_blank">{row["title"]}</a>',
                    axis=1
                )
                
                # 컬럼명 변경 후 video_id 컬럼 제거
                display_df = display_df.rename(columns={
                    'thumbnail': '썸네일',
                    'title': '제목',
                    'views': '조회수',
                    'views_subscriber_ratio': '조회수/구독자 비율',
                    'like_count': '좋아요 수',
                    'comment_count': '댓글 수',
                    '롱폼/쇼츠 여부': '롱폼/쇼츠 여부'
                })
                display_df = display_df.drop(columns=['video_id'])
                
                # HTML 테이블로 출력 (클릭 가능한 링크 포함)
                st.markdown(display_df.to_html(escape=False, index=False), unsafe_allow_html=True)
                
        except Exception as e:
            st.error(f"에러가 발생했습니다: {str(e)}")

if __name__ == "__main__":
    main()
