import streamlit as st
from googleapiclient.discovery import build
import pandas as pd
from PIL import Image
import requests
from io import BytesIO
import os
from dotenv import load_dotenv

# 앱 설정
st.set_page_config(
    page_title="Your App",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        'Get Help': None,
        'Report a bug': None,
        'About': None
    }
)


load_dotenv()

YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')

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
    
    def get_all_videos(self, channel_id):
        """Get all videos from a channel"""
        videos = []
        next_page_token = None
        
        while True:
            request = self.youtube.search().list(
                part='snippet',
                channelId=channel_id,
                maxResults=50,
                type='video',
                pageToken=next_page_token,
                order='date'  # Get newest videos first
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
                    'views': int(stats['statistics']['viewCount']),
                    'published_at': video['snippet']['publishedAt'],
                    'video_id': video['id']['videoId']
                })
            
            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                break
        
        return videos

def main():
    st.set_page_config(page_title="YouTube Channel Analyzer", layout="wide")
    
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
                
                # Get videos and create DataFrame
                videos = analyzer.get_all_videos(channel_id)
                df = pd.DataFrame(videos)
                df['views_subscriber_ratio'] = (df['views'] / channel_info['subscribers'] * 100).round(2)
                
                # Sort by views/subscriber ratio
                df = df.sort_values('views_subscriber_ratio', ascending=False).head(10)
                
                # Display videos in a table
                st.subheader("조회수/구독자 비율 TOP 10 영상")
                
                # Create a custom dataframe for display
                display_df = df[['thumbnail', 'title', 'views', 'views_subscriber_ratio']].copy()

                def format_to_10k(n):
                    num = round(n / 10000, 1)  # 만 단위로 변환하고 소수점 첫째자리에서 반올림

                    return f"{num}만"
                
                # Format numbers
                display_df['views'] = display_df['views'].apply(format_to_10k)
                display_df['views_subscriber_ratio'] = display_df['views_subscriber_ratio'].apply(lambda x: f"{int(round(x))}%")
                
                # Rename columns for display
                display_df.columns = ['썸네일', '제목', '조회수', '조회수/구독자 비율']
                
                # Display the table
                st.dataframe(
                    display_df,
                    column_config={
                        "썸네일": st.column_config.ImageColumn(
                            width="medium", 
                            help="영상 썸네일"
                        ),
                        "제목": st.column_config.Column(
                            width="large"
                        ),
                        "조회수": st.column_config.Column(
                            width="small"
                        ),
                        "조회수/구독자 비율": st.column_config.Column(
                            width="small"
                        )
                    },
                    hide_index=True,
                    use_container_width=True,
                    height=600
                )
                
        except Exception as e:
            st.error(f"에러가 발생했습니다: {str(e)}")

if __name__ == "__main__":
    main()
