#!/usr/bin/python3
from flask import Flask, jsonify, request, render_template
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta, time
import math # <--- NEW: Needed for distance calculation
from sqlalchemy import func

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///planner.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- 📍 CONFIGURATION: SET YOUR CAMPUS LOCATION HERE ---
# Example: Holberton School (Tulsa, OK coordinates as placeholder)
# Go to Google Maps, right-click your campus, and copy the numbers.
CAMPUS_LAT = 40.40663934042372
CAMPUS_LON =  49.848206791133954
MAX_DISTANCE_METERS = 50
# --- MODELS ---
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    priority = db.Column(db.String(20), default='Medium')
    status = db.Column(db.String(20), default='Pending')
    start_date = db.Column(db.String(20), nullable=True)
    due_date = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'priority': self.priority,
            'status': self.status,
            'start_date': self.start_date,
            'due_date': self.due_date
        }

class StudySession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subject = db.Column(db.String(50), nullable=False)
    duration_minutes = db.Column(db.Integer, nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return { 'subject': self.subject, 'duration': self.duration_minutes }

# NEW TABLE: Attendance
class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, default=datetime.utcnow)
    entry_time = db.Column(db.String(10), nullable=False) # Store as "09:30"
    exit_time = db.Column(db.String(10), nullable=False)  # Store as "17:00"
    valid_hours = db.Column(db.Float, nullable=False)     # Calculated hours (8am-6pm)

    def to_dict(self):
        return {
            'date': self.date.strftime('%Y-%m-%d'),
            'entry': self.entry_time,
            'exit': self.exit_time,
            'hours': self.valid_hours
        }

class WeeklyTaskSummary(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    week_start = db.Column(db.Date, nullable=False, unique=True)
    total_tasks = db.Column(db.Integer, nullable=False, default=0)
    completed_tasks = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'week_start': self.week_start.strftime('%Y-%m-%d'),
            'total_tasks': self.total_tasks,
            'completed_tasks': self.completed_tasks
        }

# --- HELPER: Calculate 8am-6pm Hours ---
def get_distance_meters(lat1, lon1, lat2, lon2):
    R = 6371000  # Radius of Earth in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2)**2 + \
        math.cos(phi1) * math.cos(phi2) * \
        math.sin(delta_lambda / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c
    
def calculate_valid_hours(entry_str, exit_str):
    # Limits
    START_LIMIT = time(8, 0)  # 08:00 AM
    END_LIMIT = time(18, 0)   # 06:00 PM

    # Parse inputs (e.g., "09:30")
    fmt = '%H:%M'
    t_entry = datetime.strptime(entry_str, fmt).time()
    t_exit = datetime.strptime(exit_str, fmt).time()

    # Clamp Entry Time (Must be at least 8:00)
    effective_entry = max(t_entry, START_LIMIT)
    
    # Clamp Exit Time (Must be at most 18:00)
    effective_exit = min(t_exit, END_LIMIT)

    # Calculate difference
    if effective_entry >= effective_exit:
        return 0.0 # Invalid duration (e.g. entered at 7pm)

    # Convert to datetime to do subtraction
    dummy_date = datetime(2000, 1, 1)
    dt_entry = datetime.combine(dummy_date, effective_entry)
    dt_exit = datetime.combine(dummy_date, effective_exit)
    
    duration = dt_exit - dt_entry
    return round(duration.total_seconds() / 3600, 2) # Return hours

def get_week_start(date_value):
    return date_value - timedelta(days=date_value.weekday())

def compute_weekly_task_summary(week_start):
    week_end = week_start + timedelta(days=7)
    tasks = Task.query.filter(
        Task.created_at >= datetime.combine(week_start, time.min),
        Task.created_at < datetime.combine(week_end, time.min)
    ).all()
    total_tasks = len(tasks)
    completed_tasks = sum(1 for task in tasks if task.status == 'Completed')
    return total_tasks, completed_tasks

def ensure_weekly_summaries():
    today = datetime.utcnow().date()
    current_week_start = get_week_start(today)
    latest_summary = WeeklyTaskSummary.query.order_by(WeeklyTaskSummary.week_start.desc()).first()

    if latest_summary is None:
        previous_week_start = current_week_start - timedelta(days=7)
        total_tasks, completed_tasks = compute_weekly_task_summary(previous_week_start)
        db.session.add(WeeklyTaskSummary(
            week_start=previous_week_start,
            total_tasks=total_tasks,
            completed_tasks=completed_tasks
        ))
        db.session.commit()
        return

    next_week_start = latest_summary.week_start + timedelta(days=7)
    if next_week_start >= current_week_start:
        return

    week_cursor = next_week_start
    while week_cursor < current_week_start:
        total_tasks, completed_tasks = compute_weekly_task_summary(week_cursor)
        db.session.add(WeeklyTaskSummary(
            week_start=week_cursor,
            total_tasks=total_tasks,
            completed_tasks=completed_tasks
        ))
        week_cursor += timedelta(days=7)
    db.session.commit()

# --- ROUTES ---
@app.route('/')
def index():
    return render_template('index.html')

# Task Routes
@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    tasks = Task.query.order_by(Task.created_at.desc()).all()
    return jsonify([task.to_dict() for task in tasks])

@app.route('/api/tasks', methods=['POST'])
def add_task():
    data = request.json
    new_task = Task(
        title=data['title'], 
        priority=data.get('priority', 'Medium'),
        start_date=data.get('start_date'),
        due_date=data.get('due_date')
    )
    db.session.add(new_task)
    db.session.commit()
    return jsonify(new_task.to_dict()), 201

@app.route('/api/tasks/<int:id>/toggle', methods=['PUT'])
def toggle_task(id):
    task = Task.query.get_or_404(id)
    task.status = 'Completed' if task.status == 'Pending' else 'Pending'
    db.session.commit()
    return jsonify(task.to_dict())

@app.route('/api/stats/tasks', methods=['GET'])
def task_stats():
    # Counts tasks by status
    stats = db.session.query(Task.status, func.count(Task.id)).group_by(Task.status).all()
    return jsonify(dict(stats))

@app.route('/api/tasks/weekly-summary', methods=['GET'])
def task_weekly_summary():
    ensure_weekly_summaries()
    summaries = WeeklyTaskSummary.query.order_by(WeeklyTaskSummary.week_start.asc()).all()

    today = datetime.utcnow().date()
    current_week_start = get_week_start(today)
    total_tasks, completed_tasks = compute_weekly_task_summary(current_week_start)

    response = [summary.to_dict() for summary in summaries]

    if not summaries or summaries[-1].week_start != current_week_start:
        response.append({
            'week_start': current_week_start.strftime('%Y-%m-%d'),
            'total_tasks': total_tasks,
            'completed_tasks': completed_tasks
        })

    return jsonify(response)

@app.route('/api/tasks/<int:id>', methods=['DELETE'])
def delete_task(id):
    task = Task.query.get_or_404(id)
    db.session.delete(task)
    db.session.commit()
    return jsonify({'message': 'Deleted'})

# Study Routes
@app.route('/api/study', methods=['POST'])
def log_study_session():
    data = request.json
    new_session = StudySession(subject=data['subject'], duration_minutes=data['duration'])
    db.session.add(new_session)
    db.session.commit()
    return jsonify(new_session.to_dict()), 201

# --- NEW ROUTE: GPS CHECK ---
@app.route('/api/attendance/check-location', methods=['POST'])
def check_location():
    data = request.json
    user_lat = data.get('lat')
    user_lon = data.get('lon')

    if user_lat is None or user_lon is None:
        return jsonify({'error': 'No coordinates provided'}), 400

    # Calculate distance
    dist = get_distance_meters(user_lat, user_lon, CAMPUS_LAT, CAMPUS_LON)
    
    if dist <= MAX_DISTANCE_METERS:
        # User is ON CAMPUS
        now_time = datetime.now().strftime('%H:%M')
        return jsonify({
            'status': 'allowed', 
            'distance': round(dist, 2),
            'time': now_time,
            'message': f'✅ Access Granted! You are {int(dist)}m from campus.'
        })
    else:
        # User is TOO FAR
        return jsonify({
            'status': 'denied', 
            'distance': round(dist, 2),
            'message': f'❌ Too far! You are {int(dist)}m away. Go to campus.'
        }), 403

# NEW: Attendance Routes
# --- UPDATED ATTENDANCE ROUTES ---

@app.route('/api/attendance', methods=['GET'])
def get_attendance():
    # Get logs for the current week (Monday to Sunday)
    today = datetime.utcnow().date()
    start_of_week = today - timedelta(days=today.weekday())
    
    # Sort by date descending (newest first)
    logs = Attendance.query.filter(Attendance.date >= start_of_week)\
                           .order_by(Attendance.date.desc(), Attendance.entry_time.desc())\
                           .all()
    
    total_hours = sum(log.valid_hours for log in logs)
    
    return jsonify({
        'logs': [log.to_dict() for log in logs],
        'total_hours': round(total_hours, 2)
    })

@app.route('/api/attendance', methods=['POST'])
def add_attendance():
    data = request.json
    
    # 1. Parse the date provided by the user
    log_date_str = data.get('date') # Format YYYY-MM-DD
    if log_date_str:
        log_date = datetime.strptime(log_date_str, '%Y-%m-%d').date()
    else:
        log_date = datetime.utcnow().date()

    # 2. CHECK: Is it a weekday? (0=Mon, 4=Fri, 5=Sat, 6=Sun)
    if log_date.weekday() > 4:
        return jsonify({'error': 'Weekends do not count towards mandatory hours!'}), 400

    # 3. Calculate hours (8am - 6pm logic)
    hours = calculate_valid_hours(data['entry'], data['exit'])
    
    new_log = Attendance(
        date=log_date,
        entry_time=data['entry'], 
        exit_time=data['exit'], 
        valid_hours=hours
    )
    db.session.add(new_log)
    db.session.commit()
    return jsonify(new_log.to_dict())


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        app.run(host='0.0.0.0', port=5000, debug=True)
