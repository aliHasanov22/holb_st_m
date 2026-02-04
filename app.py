from flask import Flask, jsonify, request, render_template
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta, time

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///planner.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- MODELS ---
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    priority = db.Column(db.String(20), default='Medium')
    status = db.Column(db.String(20), default='Pending')
    due_date = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'priority': self.priority,
            'status': self.status,
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

# --- HELPER: Calculate 8am-6pm Hours ---
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

# NEW: Attendance Routes
@app.route('/api/attendance', methods=['GET'])
def get_attendance():
    # Get start of current week (Monday)
    today = datetime.utcnow().date()
    start_of_week = today - timedelta(days=today.weekday())
    
    logs = Attendance.query.filter(Attendance.date >= start_of_week).all()
    total_hours = sum(log.valid_hours for log in logs)
    
    return jsonify({
        'logs': [log.to_dict() for log in logs],
        'total_hours': round(total_hours, 2)
    })

@app.route('/api/attendance', methods=['POST'])
def add_attendance():
    data = request.json
    hours = calculate_valid_hours(data['entry'], data['exit'])
    
    new_log = Attendance(
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
    app.run(debug=True)
