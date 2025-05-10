import os
from flask import Flask, render_template, redirect, url_for, flash, session, request
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
from dotenv import load_dotenv
import time

# .env dosyasını oku
load_dotenv()

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
REDIRECT_URI = os.getenv('REDIRECT_URI')
PLAYLIST_NAME = "Beğenilen Şarkılar 💚"

if not all([CLIENT_ID, CLIENT_SECRET, REDIRECT_URI]):
    raise ValueError("Lütfen .env dosyasında tüm gerekli değişkenleri tanımlayın.")

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Daha geniş scope izinleri
scope = "user-library-read playlist-modify-public playlist-modify-private user-read-private"

def create_spotify_oauth():
    return SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=scope,
        cache_path=None,  # Token'ı session'da saklayacağız
        show_dialog=True  # Her zaman yetkilendirme ekranını göster
    )

def get_spotify_client():
    try:
        auth_manager = create_spotify_oauth()
        
        if not session.get('token_info'):
            return None  # Token yoksa None dön, böylece login fonksiyonu çalışacak
        
        token_info = session.get('token_info')
        
        # Token süresi dolmuşsa yenile
        now = int(time.time())
        is_expired = token_info.get('expires_at', now) - now < 60

        if is_expired:
            token_info = auth_manager.refresh_access_token(token_info.get('refresh_token'))
            session['token_info'] = token_info

        return spotipy.Spotify(auth=token_info.get('access_token'))
    except Exception as e:
        session.clear()  # Hata durumunda session'ı temizle
        print(f"Spotify client hatası: {str(e)}")  # Debug için
        return None

def get_liked_songs(sp):
    try:
        all_tracks = []
        offset = 0
        limit = 50

        while True:
            results = sp.current_user_saved_tracks(limit=limit, offset=offset)
            if not results['items']:
                break

            tracks = [{'id': item['track']['id'], 
                      'name': item['track']['name'],
                      'artist': item['track']['artists'][0]['name']}
                     for item in results['items']]
            all_tracks.extend(tracks)
            
            if len(results['items']) < limit:
                break
                
            offset += limit
            
        return all_tracks
    except SpotifyException as e:
        flash(f"Beğenilen şarkılar alınırken hata: {str(e)}")
        return []

def get_or_create_playlist(sp):
    try:
        user_id = sp.current_user()['id']
        
        # Önce session'da kayıtlı playlist ID'yi kontrol et
        if session.get('playlist_id'):
            try:
                sp.playlist(session['playlist_id'])
                return session['playlist_id']
            except:
                session.pop('playlist_id', None)
        
        # Mevcut playlistleri kontrol et
        playlists = sp.current_user_playlists()
        for playlist in playlists['items']:
            if playlist['name'] == PLAYLIST_NAME:
                session['playlist_id'] = playlist['id']
                return playlist['id']
        
        # Playlist yoksa yeni oluştur
        new_playlist = sp.user_playlist_create(
            user=user_id,
            name=PLAYLIST_NAME,
            public=True,
            description='🎵 Beğenilen şarkıların otomatik senkronize edildiği playlist'
        )
        session['playlist_id'] = new_playlist['id']
        return new_playlist['id']
    except SpotifyException as e:
        flash(f"Playlist oluşturulurken hata: {str(e)}")
        return None

def sync_liked_songs_to_playlist(sp):
    try:
        liked_songs = get_liked_songs(sp)
        if not liked_songs:
            return "Beğenilen şarkılar alınamadı 😢"

        playlist_id = get_or_create_playlist(sp)
        if not playlist_id:
            return "Playlist oluşturulamadı 😢"

        # Mevcut playlist şarkılarını al
        existing_tracks = []
        offset = 0
        
        while True:
            results = sp.playlist_tracks(playlist_id, offset=offset, limit=100)
            if not results['items']:
                break
                
            existing_tracks.extend([t['track']['id'] for t in results['items'] if t['track']])
            
            if len(results['items']) < 100:
                break
                
            offset += 100

        # Yeni şarkıları ekle
        to_add = [track['id'] for track in liked_songs if track['id'] not in existing_tracks]
        
        if to_add:
            added_count = 0
            # Spotify API sınırı nedeniyle 100'er şarkı olarak böl
            for i in range(0, len(to_add), 100):
                chunk = to_add[i:i + 100]
                sp.playlist_add_items(playlist_id, chunk)
                added_count += len(chunk)
                
            song_text = "şarkı" if added_count == 1 else "şarkı"
            return f"{added_count} yeni {song_text} eklendi 🎵"
        
        return f"Playlist '{PLAYLIST_NAME}' güncel 👌"
        
    except Exception as e:
        session.clear()  # Hata durumunda session'ı temizle
        return f"Bir hata oluştu: {str(e)} 😢"

@app.route('/')
def index():
    sp = get_spotify_client()
    template_data = {
        'PLAYLIST_NAME': PLAYLIST_NAME,
        'is_authenticated': False,
        'username': None
    }
    
    if isinstance(sp, spotipy.Spotify):
        try:
            user = sp.current_user()
            template_data.update({
                'is_authenticated': True,
                'username': user['display_name']
            })
        except:
            session.clear()
    
    return render_template('index.html', **template_data)

@app.route('/login')
def login():
    auth_manager = create_spotify_oauth()
    auth_url = auth_manager.get_authorize_url()
    print(f"Auth URL: {auth_url}")  # Debug için
    return redirect(auth_url)

@app.route('/callback')
def callback():
    try:
        auth_manager = create_spotify_oauth()
        code = request.args.get('code')
        
        if not code:
            flash("Yetkilendirme başarısız: Kod alınamadı")
            return redirect(url_for('index'))
            
        token_info = auth_manager.get_access_token(code, check_cache=False)
        if not token_info:
            flash("Token alınamadı!")
            return redirect(url_for('index'))
            
        session.clear()  # Önceki oturum verilerini temizle
        session['token_info'] = token_info
        
        # Token'ı hemen test et
        sp = spotipy.Spotify(auth=token_info['access_token'])
        user = sp.current_user()
        session['username'] = user['display_name']
        flash(f"Hoş geldin {user['display_name']}! Spotify bağlantın başarılı! 🎉")
        
    except Exception as e:
        session.clear()
        flash(f"Bağlantı hatası: {str(e)}")
        
    return redirect(url_for('index'))

@app.route('/sync')
def sync():
    sp = get_spotify_client()
    if not sp:
        # Eğer client yoksa login sayfasına yönlendir
        return redirect(url_for('login'))
        
    result = sync_liked_songs_to_playlist(sp)
    flash(result)
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)