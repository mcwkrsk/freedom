from flask import Flask, request, redirect, url_for, render_template_string, send_from_directory, send_file
from datetime import datetime
from flask import session
from functools import wraps
import secrets
import hashlib
import json
import os
import math
import re
import html
from werkzeug.utils import secure_filename
import threading

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'

# Настройки для загрузки файлов
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'zip', 'rar', 'mp3', 'mp4', 'avi',
                      'mov'}
MAX_FILE_SIZE = 16 * 1024 * 1024  # 16MB

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# Создаем папку для загрузок, если она не существует
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# Класс для управления пользователями
class UserManager:
    def __init__(self):
        self.users_file = 'users.json'
        self.load_users()

    def load_users(self):
        if os.path.exists(self.users_file):
            with open(self.users_file, 'r', encoding='utf-8') as f:
                self.users = json.load(f)
        else:
            self.users = {}

    def save_users(self):
        with open(self.users_file, 'w', encoding='utf-8') as f:
            json.dump(self.users, f, ensure_ascii=False, indent=2)

    def create_guest_session(self):
        """Создает анонимную сессию для гостя"""
        guest_id = f"guest_{secrets.token_hex(8)}"
        return {
            'id': guest_id,
            'type': 'guest',
            'username': f"Гость_{guest_id[-6:]}",
            'can_post': True,
            'can_upload': False
        }

    def register_user(self, username, password):
        """Регистрирует нового пользователя"""
        if username in self.users:
            return None, "Пользователь с таким именем уже существует"

        user_id = hashlib.sha256(f"{username}{secrets.token_hex(4)}".encode()).hexdigest()[:16]
        password_hash = hashlib.sha256(password.encode()).hexdigest()

        user_data = {
            'id': user_id,
            'username': username,
            'password_hash': password_hash,
            'type': 'registered',
            'can_post': True,
            'can_upload': True,
            'registered_at': datetime.now().isoformat()
        }

        self.users[username] = user_data
        self.save_users()
        return user_data, None

    def authenticate_user(self, username, password):
        """Аутентифицирует пользователя"""
        if username not in self.users:
            return None, "Пользователь не найден"

        user_data = self.users[username]
        password_hash = hashlib.sha256(password.encode()).hexdigest()

        if user_data['password_hash'] != password_hash:
            return None, "Неверный пароль"

        return user_data, None


user_manager = UserManager()


def get_current_user():
    """Возвращает данные текущего пользователя"""
    if 'user' in session:
        return session['user']

    # Создаем гостевую сессию, если ее нет
    guest_user = user_manager.create_guest_session()
    session['user'] = guest_user
    return guest_user


def require_auth(require_upload=False):
    """Декоратор для проверки аутентификации"""
    def decorator(f):
        @wraps(f)  # Добавьте эту строку
        def decorated_function(*args, **kwargs):
            user = get_current_user()

            if require_upload and not user['can_upload']:
                return "Для загрузки файлов необходимо зарегистрироваться", 403

            if not user['can_post']:
                return "Для выполнения этого действия необходимо войти в систему", 403

            return f(*args, **kwargs)
        return decorated_function
    return decorator


# Потокобезопасное хранилище данных
class DataStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.topics = []
        self.next_topic_id = 1
        self.next_message_id = 1
        self.next_attachment_id = 1

    def get_next_topic_id(self):
        with self.lock:
            id = self.next_topic_id
            self.next_topic_id += 1
            return id

    def get_next_message_id(self):
        with self.lock:
            id = self.next_message_id
            self.next_message_id += 1
            return id

    def get_next_attachment_id(self):
        with self.lock:
            id = self.next_attachment_id
            self.next_attachment_id += 1
            return id

    def add_topic(self, topic):
        with self.lock:
            # Проверяем, нет ли уже темы с таким ID
            existing_ids = [t.id for t in self.topics]
            if topic.id in existing_ids:
                # Находим максимальный ID и устанавливаем следующий
                max_id = max(existing_ids) if existing_ids else 0
                topic.id = max_id + 1
                # Обновляем next_topic_id чтобы избежать конфликтов в будущем
                if self.next_topic_id <= topic.id:
                    self.next_topic_id = topic.id + 1
            self.topics.append(topic)

    def get_topic(self, topic_id):
        with self.lock:
            return next((t for t in self.topics if t.id == topic_id), None)

    def get_all_topics(self):
        with self.lock:
            return self.topics.copy()


data_store = DataStore()

# Константы для пагинации
MESSAGES_PER_PAGE = 5


class Attachment:
    def __init__(self, id, filename, original_filename, message_id):
        self.id = id
        self.filename = filename
        self.original_filename = original_filename
        self.message_id = message_id
        self.upload_time = datetime.now()


class Topic:
    def __init__(self, id, title, author=None):
        self.id = id
        self.title = title
        self.author = author or get_current_user()['username']
        self.messages = []
        self.created_at = datetime.now()

    def get_messages_page(self, page=1, per_page=MESSAGES_PER_PAGE):
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        return self.messages[start_idx:end_idx]

    def get_total_pages(self, per_page=MESSAGES_PER_PAGE):
        return max(1, math.ceil(len(self.messages) / per_page))

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'author': self.author,
            'messages_count': len(self.messages),
            'first_message_preview': self.messages[0].text[:100] + '...' if self.messages and len(
                self.messages[0].text) > 100 else self.messages[0].text if self.messages else 'Нет сообщений',
            'created_at': self.created_at.strftime('%d.%m.%Y в %H:%M')  # Изменен формат даты
        }


class Message:
    def __init__(self, id, text, author=None):
        self.id = id
        self.text = text
        self.author = author or get_current_user()['username']
        self.timestamp = datetime.now()
        self.attachments = []
        self.formatted_text = self.format_text()

    def format_text(self):
        if not self.text:
            return ""

        # Безопасное экранирование HTML
        text = html.escape(self.text)

        # Обработка таблиц перед другими форматированиями
        text = self.process_tables(text)

        # Замена переносов строк на <br>
        text = text.replace('\n', '<br>')

        # Простые замены для форматирования
        text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'\*(.*?)\*', r'<em>\1</em>', text)
        text = re.sub(r'~~(.*?)~~', r'<del>\1</del>', text)
        text = re.sub(r'`(.*?)`', r'<code>\1</code>', text)

        # Обработка ссылок
        text = re.sub(r'\[(.*?)\]\((.*?)\)', r'<a href="\2" target="_blank">\1</a>', text)

        return text

    def process_tables(self, text):
        try:
            # Упрощенная и более надежная обработка таблиц
            table_pattern = r'\|\|([^|].*?[^|])\|\|'
            tables = re.findall(table_pattern, text, re.DOTALL)

            for table_content in tables:
                rows = [row.strip() for row in table_content.split('||') if row.strip()]
                if len(rows) < 2:
                    continue

                html_table = '<table class="forum-table">'

                # Первая строка - заголовок
                html_table += '<thead><tr>'
                headers = rows[0].split('|')
                for header in headers:
                    html_table += f'<th>{header.strip()}</th>'
                html_table += '</tr></thead>'

                # Остальные строки - данные
                html_table += '<tbody>'
                for row in rows[1:]:
                    html_table += '<tr>'
                    cells = row.split('|')
                    for cell in cells:
                        html_table += f'<td>{cell.strip()}</td>'
                    html_table += '</tr>'
                html_table += '</tbody></table>'

                text = text.replace(f'||{table_content}||', html_table)

            return text
        except Exception as e:
            # В случае ошибки возвращаем исходный текст
            return text


def get_frontend_path():
    """Более надежное получение пути к фронтенду"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    frontend_path = os.path.join(current_dir, 'frontend')

    # Если папка frontend не найдена, пытаемся найти в родительской директории
    if not os.path.exists(frontend_path):
        frontend_path = os.path.join(current_dir, '../frontend')

    if not os.path.exists(frontend_path):
        # Создаем базовую структуру, если папка не существует
        os.makedirs(frontend_path, exist_ok=True)
        create_default_templates(frontend_path)

    return frontend_path


def create_default_templates(frontend_path):
    """Создает базовые шаблоны, если они отсутствуют"""
    templates = {
        'index.html': """
<!DOCTYPE html>
<html>
<head>
    <title>Форум</title>
    <link rel="stylesheet" href="/style.css">
</head>
<body>
    <div class="container">
        <h1>Форум</h1>

        <div class="user-info">
            {% if user.type == 'guest' %}
                <p>Вы вошли как <strong>Гость</strong>. 
                   <a href="/login">Войдите</a> или <a href="/register">зарегистрируйтесь</a> чтобы писать сообщения.</p>
            {% else %}
                <p>Вы вошли как <strong>{{ user.username }}</strong>. 
                   <a href="/profile">Профиль</a> | <a href="/logout">Выйти</a></p>
            {% endif %}
        </div>

        <div class="forum-actions">
            <a href="/new_topic.html" class="button">Создать новую тему</a>
            <a href="/formatting_help.html" class="button" target="_blank">Справка по форматированию</a>
        </div>

        <div class="search-form">
            <form method="GET" action="/">
                <input type="text" name="q" value="{{ search_query }}" placeholder="Поиск по темам и сообщениям...">
                <button type="submit">Найти</button>
                {% if search_query %}
                <a href="/" class="button">Показать все темы</a>
                {% endif %}
            </form>
        </div>

        <h2>
            {% if search_query %}
            Результаты поиска для "{{ search_query }}" (найдено: {{ topics|length }})
            {% else %}
            Темы форума (всего: {{ topics|length }})
            {% endif %}
        </h2>

        {% if topics %}
        <div class="topics-list">
            {% for topic in topics %}
            <div class="topic">
                <h3><a href="/topic/{{ topic.id }}">{{ topic.title }}</a></h3>
                <p>Автор: {{ topic.author }} | Сообщений: {{ topic.messages_count }} | Создана: {{ topic.created_at }}</p>
                <div class="topic-preview">
                    {{ topic.first_message_preview }}
                </div>
            </div>
            {% endfor %}
        </div>
        {% else %}
        <div class="no-results">
            {% if search_query %}
            <p>По запросу "{{ search_query }}" ничего не найдено.</p>
            {% else %}
            <p>Пока нет ни одной темы. <a href="/new_topic.html">Создайте первую!</a></p>
            {% endif %}
        </div>
        {% endif %}
    </div>
</body>
</html>
        """,

        'topic.html': """
<!DOCTYPE html>
<html>
<head>
    <title>Тема: {{ topic.title }}</title>
    <link rel="stylesheet" href="/style.css">
</head>
<body>
    <div class="container">
        <h1>{{ topic.title }}</h1>

        <div class="user-info">
            {% if user.type == 'guest' %}
                <p>Вы вошли как <strong>Гость</strong>. 
                   <a href="/login">Войдите</a> или <a href="/register">зарегистрируйтесь</a> чтобы отвечать в темах.</p>
            {% else %}
                <p>Вы вошли как <strong>{{ user.username }}</strong>. 
                   <a href="/profile">Профиль</a> | <a href="/logout">Выйти</a></p>
            {% endif %}
        </div>

        <div class="topic-actions">
            <a href="/" class="button">← Назад к списку тем</a>
            <a href="/formatting_help.html" class="button" target="_blank">Справка по форматированию</a>
        </div>

        <div class="pagination-info">
            <p>Сообщений: {{ topic.messages|length }} | Страница {{ current_page }} из {{ total_pages }}</p>
        </div>

        {% if total_pages > 1 %}
        <div class="pagination">
            {% if current_page > 1 %}
                <a href="/topic/{{ topic.id }}?page=1" class="page-link">Первая</a>
                <a href="/topic/{{ topic.id }}?page={{ current_page - 1 }}" class="page-link">← Назад</a>
            {% endif %}

            {% for page_num in range(1, total_pages + 1) %}
                {% if page_num == current_page %}
                    <span class="current-page">{{ page_num }}</span>
                {% elif page_num >= current_page - 2 and page_num <= current_page + 2 %}
                    <a href="/topic/{{ topic.id }}?page={{ page_num }}" class="page-link">{{ page_num }}</a>
                {% elif page_num == 1 or page_num == total_pages %}
                    <a href="/topic/{{ topic.id }}?page={{ page_num }}" class="page-link">{{ page_num }}</a>
                {% elif page_num == current_page - 3 or page_num == current_page + 3 %}
                    <span class="ellipsis">...</span>
                {% endif %}
            {% endfor %}

            {% if current_page < total_pages %}
                <a href="/topic/{{ topic.id }}?page={{ current_page + 1 }}" class="page-link">Вперед →</a>
                <a href="/topic/{{ topic.id }}?page={{ total_pages }}" class="page-link">Последняя</a>
            {% endif %}
        </div>
        {% endif %}

        <div class="messages">
            {% for message in messages %}
            <div class="message">
                <div class="message-header">
                    <strong>{{ message.author }}</strong>
                    <small>{{ message.timestamp.strftime('%Y-%m-%d %H:%M:%S') }}</small>
                </div>
                <div class="message-content">
                    {{ message.formatted_text | safe }}
                </div>

                {% if message.attachments %}
                <div class="attachments">
                    <strong>Прикрепленные файлы:</strong>
                    <ul>
                        {% for attachment in message.attachments %}
                        <li>
                            <a href="/download/{{ attachment.filename }}" class="attachment-link" target="_blank">
                                📎 {{ attachment.original_filename }}
                            </a>
                            <small>({{ attachment.upload_time.strftime('%Y-%m-%d %H:%M') }})</small>
                        </li>
                        {% endfor %}
                    </ul>
                </div>
                {% endif %}
            </div>
            {% else %}
            <div class="no-messages">
                <p>В этой теме пока нет сообщений.</p>
            </div>
            {% endfor %}
        </div>

        {% if total_pages > 1 %}
        <div class="pagination">
            {% if current_page > 1 %}
                <a href="/topic/{{ topic.id }}?page=1" class="page-link">Первая</a>
                <a href="/topic/{{ topic.id }}?page={{ current_page - 1 }}" class="page-link">← Назад</a>
            {% endif %}

            {% for page_num in range(1, total_pages + 1) %}
                {% if page_num == current_page %}
                    <span class="current-page">{{ page_num }}</span>
                {% elif page_num >= current_page - 2 and page_num <= current_page + 2 %}
                    <a href="/topic/{{ topic.id }}?page={{ page_num }}" class="page-link">{{ page_num }}</a>
                {% elif page_num == 1 or page_num == total_pages %}
                    <a href="/topic/{{ topic.id }}?page={{ page_num }}" class="page-link">{{ page_num }}</a>
                {% elif page_num == current_page - 3 or page_num == current_page + 3 %}
                    <span class="ellipsis">...</span>
                {% endif %}
            {% endfor %}

            {% if current_page < total_pages %}
                <a href="/topic/{{ topic.id }}?page={{ current_page + 1 }}" class="page-link">Вперед →</a>
                <a href="/topic/{{ topic.id }}?page={{ total_pages }}" class="page-link">Последняя</a>
            {% endif %}
        </div>
        {% endif %}

        {% if user.can_post %}
        <div class="message-form">
            <h3>Добавить сообщение</h3>
            <form method="POST" action="/topic/{{ topic.id }}/reply" enctype="multipart/form-data">
                <textarea name="text" required placeholder="Текст сообщения"></textarea>
                <div class="formatting-hint">
                    <small>Поддерживается: **жирный**, *курсив*, ~~зачеркнутый~~, `код`, [ссылки](http://example.com), таблицы</small>
                </div>

                {% if user.can_upload %}
                <div class="form-group">
                    <label for="files">Прикрепить файлы:</label>
                    <input type="file" id="files" name="files" multiple>
                    <div class="formatting-hint">
                        <small>Можно выбрать несколько файлов. Разрешены: txt, pdf, png, jpg, jpeg, gif, doc, docx, zip, rar, mp3, mp4, avi, mov. Макс. размер: 16MB.</small>
                    </div>
                </div>
                {% else %}
                <div class="upload-restricted">
                    <p>❌ Загрузка файлов недоступна для гостей. <a href="/register">Зарегистрируйтесь</a> чтобы получить возможность загружать файлы.</p>
                </div>
                {% endif %}

                <button type="submit">Отправить</button>
            </form>
        </div>
        {% else %}
        <div class="post-restricted">
            <p>❌ Для написания сообщений необходимо <a href="/login">войти в систему</a>.</p>
        </div>
        {% endif %}
    </div>
</body>
</html>
        """,

        'new_topic.html': """
<!DOCTYPE html>
<html>
<head>
    <title>Создать новую тему</title>
    <link rel="stylesheet" href="/style.css">
</head>
<body>
    <div class="container">
        <h1>Создать новую тему</h1>

        <div class="user-info">
            {% if user.type == 'guest' %}
                <p>Вы вошли как <strong>Гость</strong>. 
                   <a href="/login">Войдите</a> или <a href="/register">зарегистрируйтесь</a> чтобы создавать темы.</p>
            {% else %}
                <p>Вы вошли как <strong>{{ user.username }}</strong>. 
                   <a href="/profile">Профиль</a> | <a href="/logout">Выйти</a></p>
            {% endif %}
        </div>

        <div class="topic-actions">
            <a href="/" class="button">← Назад к списку тем</a>
            <a href="/formatting_help.html" class="button" target="_blank">Справка по форматированию</a>
        </div>

        {% if user.can_post %}
        <form method="POST" action="/new_topic" enctype="multipart/form-data">
            <div class="form-group">
                <label for="title">Заголовок темы:</label>
                <input type="text" id="title" name="title" required>
            </div>

            <div class="form-group">
                <label for="text">Первое сообщение:</label>
                <textarea id="text" name="text" required placeholder="Текст сообщения (поддерживается форматирование)"></textarea>
                <div class="formatting-hint">
                     <small>Поддерживается: **жирный**, *курсив*, ~~зачеркнутый~~, `код`, [ссылки](http://example.com), таблицы</small>
                </div>
            </div>

            {% if user.can_upload %}
            <div class="form-group">
                <label for="files">Прикрепить файлы:</label>
                <input type="file" id="files" name="files" multiple>
                <div class="formatting-hint">
                    <small>Можно выбрать несколько файлов. Разрешены: txt, pdf, png, jpg, jpeg, gif, doc, docx, zip, rar, mp3, mp4, avi, mov. Макс. размер: 16MB.</small>
                </div>
            </div>
            {% else %}
            <div class="upload-restricted">
                <p>❌ Загрузка файлов недоступна для гостей. <a href="/register">Зарегистрируйтесь</a> чтобы получить возможность загружать файлы.</p>
            </div>
            {% endif %}

            <button type="submit">Создать тему</button>
        </form>
        {% else %}
        <div class="post-restricted">
            <p>❌ Для создания тем необходимо <a href="/login">войти в систему</a>.</p>
        </div>
        {% endif %}
    </div>
</body>
</html>
        """,

        'login.html': """
<!DOCTYPE html>
<html>
<head>
    <title>Вход в систему</title>
    <link rel="stylesheet" href="/style.css">
</head>
<body>
    <div class="container">
        <h1>Вход в систему</h1>
        <a href="/" class="button">← Назад к форуму</a>

        {% if error %}
        <div class="error">{{ error }}</div>
        {% endif %}

        <form method="POST">
            <div class="form-group">
                <label for="username">Имя пользователя:</label>
                <input type="text" id="username" name="username" required>
            </div>

            <div class="form-group">
                <label for="password">Пароль:</label>
                <input type="password" id="password" name="password" required>
            </div>

            <button type="submit">Войти</button>
        </form>

        <p>Нет аккаунта? <a href="/register">Зарегистрируйтесь</a></p>
        <p>Или продолжите как <a href="/">гость</a> (можно читать, но нельзя писать)</p>
    </div>
</body>
</html>
        """,

        'register.html': """
<!DOCTYPE html>
<html>
<head>
    <title>Регистрация</title>
    <link rel="stylesheet" href="/style.css">
</head>
<body>
    <div class="container">
        <h1>Регистрация</h1>
        <a href="/" class="button">← Назад к форуму</a>

        {% if error %}
        <div class="error">{{ error }}</div>
        {% endif %}

        <form method="POST">
            <div class="form-group">
                <label for="username">Имя пользователя:</label>
                <input type="text" id="username" name="username" required minlength="3">
                <small>Минимум 3 символа</small>
            </div>

            <div class="form-group">
                <label for="password">Пароль:</label>
                <input type="password" id="password" name="password" required minlength="6">
                <small>Минимум 6 символов</small>
            </div>

            <div class="form-group">
                <label for="confirm_password">Подтвердите пароль:</label>
                <input type="password" id="confirm_password" name="confirm_password" required>
            </div>

            <button type="submit">Зарегистрироваться</button>
        </form>

        <p>Уже есть аккаунт? <a href="/login">Войдите</a></p>
    </div>
</body>
</html>
        """,

        'profile.html': """
<!DOCTYPE html>
<html>
<head>
    <title>Профиль пользователя</title>
    <link rel="stylesheet" href="/style.css">
</head>
<body>
    <div class="container">
        <h1>Профиль пользователя</h1>
        <a href="/" class="button">← Назад к форуму</a>

        <div class="profile-info">
            <p><strong>Имя:</strong> {{ user.username }}</p>
            <p><strong>Тип:</strong> 
                {% if user.type == 'guest' %}Гость
                {% elif user.type == 'registered' %}Зарегистрированный пользователь
                {% else %}Анонимный пользователь
                {% endif %}
            </p>
            <p><strong>Права:</strong> 
                {% if user.can_post %}✅ Может писать сообщения{% else %}❌ Только чтение{% endif %},
                {% if user.can_upload %}✅ может загружать файлы{% else %}❌ не может загружать файлы{% endif %}
            </p>
            {% if user.registered_at %}
            <p><strong>Зарегистрирован:</strong> {{ user.registered_at[:10] }}</p>
            {% endif %}
        </div>

        {% if user.type == 'guest' %}
        <div class="auth-actions">
            <a href="/login" class="button">Войти в существующий аккаунт</a>
            <a href="/register" class="button">Зарегистрироваться</a>
        </div>
        {% else %}
        <a href="/logout" class="button">Выйти</a>
        {% endif %}
    </div>
</body>
</html>
        """,

        'formatting_help.html': """
<!DOCTYPE html>
<html>
<head>
    <title>Справка по форматированию</title>
    <link rel="stylesheet" href="/style.css">
</head>
<body>
    <div class="container">
        <h1>Справка по форматированию текста</h1>

        <a href="/" class="button">← Назад к форуму</a>

        <div class="formatting-help">
            <h2>Поддерживаемые стили форматирования</h2>

            <table class="formatting-table">
                <thead>
                    <tr>
                        <th>Синтаксис</th>
                        <th>Результат</th>
                        <th>Пример</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>**жирный текст**</td>
                        <td><strong>жирный текст</strong></td>
                        <td>Это **важно**</td>
                    </tr>
                    <tr>
                        <td>*курсив*</td>
                        <td><em>курсив</em></td>
                        <td>Это *интересно*</td>
                    </tr>
                    <tr>
                        <td>~~зачеркнутый~~</td>
                        <td><del>зачеркнутый</del></td>
                        <td>Это ~~неправильно~~</td>
                    </tr>
                    <tr>
                        <td>`код`</td>
                        <td><code>код</code></td>
                        <td>Используйте `print()`</td>
                    </tr>
                    <tr>
                        <td>[текст](URL)</td>
                        <td><a href="#">текст</a></td>
                        <td>[Google](http://google.com)</td>
                    </tr>
                    <tr>
                        <td>Перенос строки</td>
                        <td>Просто новая строка</td>
                        <td>Первая строка<br>Вторая строка</td>
                    </tr>
                    <tr>
                        <td>Таблицы (см. ниже)</td>
                        <td>HTML таблица</td>
                        <td>Специальный синтаксис</td>
                    </tr>
                </tbody>
            </table>

            <h2>Создание таблиц</h2>

            <p>Для создания таблиц используйте специальный синтаксис:</p>

            <div class="table-example">
||Заголовок 1|Заголовок 2|Заголовок 3||
||Ячейка 1|Ячейка 2|Ячейка 3||
||Данные 1|Данные 2|Данные 3||</div>

            <p>Результат:</p>

            <table class="forum-table">
                <thead>
                    <tr>
                        <th>Заголовок 1</th>
                        <th>Заголовок 2</th>
                        <th>Заголовок 3</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>Ячейка 1</td>
                        <td>Ячейка 2</td>
                        <td>Ячейка 3</td>
                    </tr>
                    <tr>
                        <td>Данные 1</td>
                        <td>Данные 2</td>
                        <td>Данные 3</td>
                    </tr>
                </tbody>
            </table>

            <h3>Правила создания таблиц:</h3>
            <ul>
                <li>Каждая строка таблицы начинается и заканчивается символами <code>||</code></li>
                <li>Ячейки разделяются символом <code>|</code></li>
                <li>Первая строка становится заголовком таблицы</li>
                <li>Последующие строки становятся строками данных</li>
                <li>Количество ячеек в каждой строке должно быть одинаковым</li>
            </ul>

            <h2>Примеры</h2>

            <div class="example">
                <h3>Исходный текст:</h3>
                <pre>
Привет всем!

Это **важное** сообщение с *разными* стилями.

Код примера:
`print("Hello World")`

Таблица с данными:
||Имя|Возраст|Город||
||Анна|25|Москва||
||Иван|30|Санкт-Петербург||

Ссылка: [Перейти на Google](http://google.com)
                </pre>

                <h3>Результат:</h3>
                <div class="example-result">
                    <p>Привет всем!</p>
                    <p>Это <strong>важное</strong> сообщение с <em>разными</em> стилями.</p>
                    <p>Код примера:<br><code>print("Hello World")</code></p>
                    <p>Таблица с данными:</p>
                    <table class="forum-table">
                        <thead>
                            <tr>
                                <th>Имя</th>
                                <th>Возраст</th>
                                <th>Город</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr>
                                <td>Анна</td>
                                <td>25</td>
                                <td>Москва</td>
                            </tr>
                            <tr>
                                <td>Иван</td>
                                <td>30</td>
                                <td>Санкт-Петербург</td>
                            </tr>
                        </tbody>
                    </table>
                    <p>Ссылка: <a href="http://google.com" target="_blank">Перейти на Google</a></p>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
        """,

        'style.css': """
body {
    font-family: Arial, sans-serif;
    max-width: 800px;
    margin: 0 auto;
    padding: 20px;
    line-height: 1.6;
    background-color: #f5f5f5;
}

.container {
    background-color: white;
    border: 1px solid #ddd;
    padding: 20px;
    border-radius: 5px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.topic {
    border: 1px solid #ddd;
    padding: 15px;
    margin: 10px 0;
    border-radius: 5px;
    background-color: #f9f9f9;
}

.topic h3 {
    margin-top: 0;
}

.topic a {
    text-decoration: none;
    color: #333;
}

.topic a:hover {
    color: #4CAF50;
}

.topic-preview {
    margin-top: 10px;
    color: #666;
}

.message {
    border: 1px solid #eee;
    padding: 15px;
    margin: 15px 0;
    border-radius: 5px;
    background-color: #f9f9f9;
}

.message-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
    padding-bottom: 5px;
    border-bottom: 1px solid #eee;
}

.message-content {
    margin-bottom: 10px;
    line-height: 1.5;
}

.message small {
    color: #666;
    font-size: 0.9em;
}

.button {
    display: inline-block;
    padding: 8px 15px;
    background-color: #4CAF50;
    color: white;
    text-decoration: none;
    border-radius: 4px;
    margin-bottom: 10px;
    margin-right: 10px;
    border: none;
    cursor: pointer;
    font-size: 14px;
}

.button:hover {
    background-color: #45a049;
}

form {
    margin-top: 20px;
}

.form-group {
    margin-bottom: 15px;
}

label {
    display: block;
    margin-bottom: 5px;
    font-weight: bold;
}

input[type="text"], input[type="password"], textarea {
    width: 100%;
    padding: 8px;
    border: 1px solid #ddd;
    border-radius: 4px;
    box-sizing: border-box;
    font-size: 14px;
}

textarea {
    height: 100px;
    resize: vertical;
}

button {
    padding: 10px 20px;
    background-color: #4CAF50;
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 16px;
}

button:hover {
    background-color: #45a049;
}

.search-form {
    margin-bottom: 20px;
    padding: 15px;
    background-color: #f5f5f5;
    border-radius: 5px;
}

.search-form form {
    display: flex;
    gap: 10px;
    align-items: center;
    margin: 0;
}

.search-form input[type="text"] {
    flex: 1;
    margin: 0;
}

.search-form button {
    margin: 0;
}

.no-results, .no-messages {
    text-align: center;
    padding: 40px;
    color: #666;
}

/* Стили для пагинации */
.pagination {
    display: flex;
    justify-content: center;
    align-items: center;
    flex-wrap: wrap;
    gap: 5px;
    margin: 20px 0;
    padding: 10px;
    background-color: #f9f9f9;
    border-radius: 5px;
}

.page-link {
    padding: 5px 10px;
    border: 1px solid #ddd;
    border-radius: 3px;
    text-decoration: none;
    color: #333;
}

.page-link:hover {
    background-color: #4CAF50;
    color: white;
}

.current-page {
    padding: 5px 10px;
    background-color: #4CAF50;
    color: white;
    border-radius: 3px;
    font-weight: bold;
}

.ellipsis {
    padding: 5px;
}

.pagination-info {
    text-align: center;
    margin: 10px 0;
    color: #666;
    font-style: italic;
}

/* Стили для форматированного текста */
strong {
    font-weight: bold;
}

em {
    font-style: italic;
}

del {
    text-decoration: line-through;
}

code {
    background-color: #f1f1f1;
    padding: 2px 4px;
    border-radius: 3px;
    font-family: 'Courier New', monospace;
}

a {
    color: #4CAF50;
    text-decoration: none;
}

a:hover {
    text-decoration: underline;
}

.formatting-hint {
    margin-top: 5px;
    color: #666;
    font-size: 0.9em;
}

.topic-actions, .forum-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 20px;
}

/* Стили для таблиц */
.forum-table {
    width: 100%;
    border-collapse: collapse;
    margin: 15px 0;
    background-color: white;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.forum-table th {
    background-color: #4CAF50;
    color: white;
    font-weight: bold;
    padding: 10px;
    text-align: left;
    border: 1px solid #ddd;
}

.forum-table td {
    padding: 10px;
    border: 1px solid #ddd;
}

.forum-table tr:nth-child(even) {
    background-color: #f9f9f9;
}

.forum-table tr:hover {
    background-color: #f1f1f1;
}

.table-example {
    margin: 15px 0;
    padding: 10px;
    background-color: #f5f5f5;
    border-radius: 5px;
    font-family: monospace;
    white-space: pre-wrap;
}

/* Стили для загрузки файлов и вложений */
.form-group input[type="file"] {
    padding: 5px;
    border: 1px solid #ddd;
    border-radius: 4px;
    background-color: white;
    width: 100%;
}

.attachments {
    margin-top: 10px;
    padding: 10px;
    background-color: #f0f0f0;
    border-radius: 5px;
    border-left: 3px solid #4CAF50;
}

.attachments ul {
    margin: 5px 0;
    padding-left: 0;
    list-style-type: none;
}

.attachments li {
    margin: 8px 0;
    padding: 5px;
    background-color: white;
    border-radius: 3px;
    display: flex;
    align-items: center;
    justify-content: space-between;
}

.attachment-link {
    color: #4CAF50;
    text-decoration: none;
    font-weight: bold;
}

.attachment-link:hover {
    text-decoration: underline;
}

.attachments small {
    color: #666;
    font-size: 0.8em;
}

/* Улучшения для форм */
.form-group {
    margin-bottom: 20px;
}

.form-group label {
    display: block;
    margin-bottom: 8px;
    font-weight: bold;
    color: #333;
}

/* Стили для информации о пользователе */
.user-info {
    background-color: #e8f5e8;
    padding: 10px;
    border-radius: 4px;
    margin-bottom: 15px;
    border-left: 4px solid #4CAF50;
}

.user-info p {
    margin: 0;
}

/* Стили для ошибок */
.error {
    color: #d32f2f;
    background-color: #ffcdd2;
    padding: 10px;
    border-radius: 4px;
    margin: 10px 0;
    border-left: 4px solid #d32f2f;
}

/* Стили для профиля */
.profile-info {
    background-color: #f5f5f5;
    padding: 15px;
    border-radius: 5px;
    margin: 15px 0;
}

.auth-actions {
    display: flex;
    gap: 10px;
    margin: 15px 0;
}

/* Стили для ограничений */
.upload-restricted, .post-restricted {
    background-color: #fff3cd;
    padding: 10px;
    border-radius: 4px;
    margin: 10px 0;
    border-left: 4px solid #ffc107;
}

.upload-restricted p, .post-restricted p {
    margin: 0;
    color: #856404;
}
"""
    }

    for filename, content in templates.items():
        filepath = os.path.join(frontend_path, filename)
        # Создаем файл только если он не существует
        if not os.path.exists(filepath):
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)


def load_template(name):
    """Загрузка шаблона с обработкой ошибок"""
    try:
        frontend_path = get_frontend_path()
        template_path = os.path.join(frontend_path, name)

        if not os.path.exists(template_path):
            return f"<h1>Шаблон {name} не найден</h1><p>Создан базовый шаблон. Перезагрузите страницу.</p>"

        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"<h1>Ошибка загрузки шаблона</h1><p>{str(e)}</p>"


def search_topics(query):
    """Поиск тем с обработкой ошибок"""
    try:
        if not query:
            return [topic.to_dict() for topic in data_store.get_all_topics()]

        query_lower = query.lower()
        results = []

        for topic in data_store.get_all_topics():
            if query_lower in topic.title.lower():
                results.append(topic.to_dict())
                continue

            for message in topic.messages:
                if query_lower in message.text.lower():
                    results.append(topic.to_dict())
                    break

        return results
    except Exception as e:
        # В случае ошибки возвращаем пустой список
        return []


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def save_uploaded_file(file, message_id):
    user = get_current_user()
    if not user['can_upload']:
        return None  # Гости не могут загружать файлы

    try:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            unique_filename = f"{data_store.get_next_attachment_id()}_{filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(filepath)

            attachment = Attachment(data_store.get_next_attachment_id(), unique_filename, filename, message_id)
            return attachment
        return None
    except Exception as e:
        print(f"Ошибка при сохранении файла: {e}")
        return None


# Маршруты Flask
@app.route('/style.css')
def serve_css():
    return send_from_directory(get_frontend_path(), 'style.css')


@app.route('/')
def index():
    try:
        search_query = request.args.get('q', '')
        user = get_current_user()  # Добавлено

        if search_query:
            filtered_topics = search_topics(search_query)
        else:
            filtered_topics = [topic.to_dict() for topic in data_store.get_all_topics()]

        filtered_topics.sort(key=lambda x: x['id'], reverse=True)

        template = load_template('index.html')
        return render_template_string(template, topics=filtered_topics, search_query=search_query, user=user)  # Добавлено user
    except Exception as e:
        return f"<h1>Ошибка</h1><p>{str(e)}</p>", 500


@app.route('/new_topic.html')
def new_topic_form():
    user = get_current_user()  # Добавлено
    template = load_template('new_topic.html')
    return render_template_string(template, user=user)  # Добавлено user


@app.route('/new_topic', methods=['POST'])
@require_auth()
def create_topic():
    try:
        title = request.form['title']
        text = request.form['text']

        # Получаем следующий доступный ID
        new_topic_id = data_store.get_next_topic_id()
        topic = Topic(new_topic_id, title)

        if text and text.strip():
            message = Message(data_store.get_next_message_id(), text)

            if 'files' in request.files:
                files = request.files.getlist('files')
                for file in files:
                    if file.filename:
                        attachment = save_uploaded_file(file, message.id)
                        if attachment:
                            message.attachments.append(attachment)

            topic.messages.append(message)

        data_store.add_topic(topic)
        return redirect('/')
    except Exception as e:
        return f"<h1>Ошибка создания темы</h1><p>{str(e)}</p>", 500


@app.route('/topic/<int:topic_id>')
def view_topic(topic_id):
    try:
        page = request.args.get('page', 1, type=int)
        if page < 1:
            page = 1

        topic = data_store.get_topic(topic_id)
        if not topic:
            return "Тема не найдена", 404

        total_pages = topic.get_total_pages()
        if page > total_pages:
            page = total_pages

        messages_page = topic.get_messages_page(page)
        user = get_current_user()  # Добавлено

        template = load_template('topic.html')
        return render_template_string(template,
                                      topic=topic,
                                      messages=messages_page,
                                      current_page=page,
                                      total_pages=total_pages,
                                      user=user)  # Добавлено user
    except Exception as e:
        return f"<h1>Ошибка</h1><p>{str(e)}</p>", 500


@app.route('/topic/<int:topic_id>/reply', methods=['POST'])
@require_auth()
def reply_to_topic(topic_id):
    try:
        topic = data_store.get_topic(topic_id)
        if not topic:
            return "Тема не найдена", 404

        text = request.form['text']
        if text and text.strip():
            message = Message(data_store.get_next_message_id(), text)

            if 'files' in request.files:
                files = request.files.getlist('files')
                for file in files:
                    if file.filename:
                        attachment = save_uploaded_file(file, message.id)
                        if attachment:
                            message.attachments.append(attachment)

            topic.messages.append(message)

        total_pages = topic.get_total_pages()
        return redirect(f'/topic/{topic_id}?page={total_pages}')
    except Exception as e:
        return f"<h1>Ошибка ответа</h1><p>{str(e)}</p>", 500


@app.route('/download/<filename>')
def download_file(filename):
    try:
        return send_file(os.path.join(app.config['UPLOAD_FOLDER'], filename),
                         as_attachment=True,
                         download_name=filename.split('_', 1)[1] if '_' in filename else filename)
    except Exception as e:
        return "Файл не найден", 404


@app.route('/formatting_help.html')
def formatting_help():
    user = get_current_user()  # Добавлено
    template = load_template('formatting_help.html')
    return render_template_string(template, user=user)  # Добавлено user


def init_test_data():
    if not data_store.get_all_topics():
        # Создаем тестовые темы с явными ID
        topic1 = Topic(1, "Пример темы с таблицей", author="Система")
        table_example = """||Заголовок 1|Заголовок 2|Заголовок 3||
||Ячейка 1|Ячейка 2|Ячейка 3||
||Данные 1|Данные 2|Данные 3||"""

        topic1.messages.append(Message(1,
                                      f"Вот пример таблицы:\n\n{table_example}\n\nТаблица создается с помощью специального синтаксиса.",
                                      author="Система"))
        data_store.add_topic(topic1)

        topic2 = Topic(2, "Вторая тема", author="Система")
        topic2.messages.append(Message(2, "Сообщение во второй теме", author="Система"))
        data_store.add_topic(topic2)

        # Устанавливаем next_topic_id на следующий после максимального существующего ID
        max_id = max([t.id for t in data_store.get_all_topics()])
        data_store.next_topic_id = max_id + 1

# Маршруты для аутентификации - добавьте user в вызовы
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        user_data, error = user_manager.authenticate_user(username, password)
        if error:
            user = get_current_user()  # Добавлено
            return render_template_string(load_template('login.html'), error=error, user=user)  # Добавлено user

        session['user'] = user_data
        return redirect('/')

    user = get_current_user()  # Добавлено
    return render_template_string(load_template('login.html'), user=user)  # Добавлено user


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        confirm_password = request.form['confirm_password']

        if password != confirm_password:
            user = get_current_user()  # Добавлено
            return render_template_string(load_template('register.html'), error="Пароли не совпадают", user=user)  # Добавлено user

        if len(username) < 3:
            user = get_current_user()  # Добавлено
            return render_template_string(load_template('register.html'),
                                          error="Имя пользователя должно быть не менее 3 символов", user=user)  # Добавлено user

        if len(password) < 6:
            user = get_current_user()  # Добавлено
            return render_template_string(load_template('register.html'),
                                          error="Пароль должен быть не менее 6 символов", user=user)  # Добавлено user

        user_data, error = user_manager.register_user(username, password)
        if error:
            user = get_current_user()  # Добавлено
            return render_template_string(load_template('register.html'), error=error, user=user)  # Добавлено user

        session['user'] = user_data
        return redirect('/')

    user = get_current_user()  # Добавлено
    return render_template_string(load_template('register.html'), user=user)  # Добавлено user


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')


@app.route('/profile')
def profile():
    user = get_current_user()
    return render_template_string(load_template('profile.html'), user=user)


if __name__ == '__main__':
    init_test_data()
    app.run(debug=True, host='0.0.0.0', port=5000)