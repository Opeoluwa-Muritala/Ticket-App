import utils.http_session
import utils.transport
import utils.session
from flask import Flask, render_template, request, redirect, flash, url_for, send_from_directory, session
from flask_mail import Mail, Message
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SelectField
from flask_wtf.file import FileField, FileAllowed
from wtforms.validators import DataRequired, Email, Length, Regexp
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import mysql.connector, uuid, os

load_dotenv()

app = Flask(__name__)
app.secret_key = 'super-secret-development-key'

# Mail configuration (Mailtrap settings)
app.config['MAIL_SERVER'] = 'sandbox.smtp.mailtrap.io'
app.config['MAIL_PORT'] = 2525
app.config['MAIL_USERNAME'] = '0a860740818967'
app.config['MAIL_PASSWORD'] = os.getenv("PASSWORD")
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
mail = Mail(app)

# File uploads
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024
UPLOAD_FOLDER = 'uploads'
app.config['WTF_CSRF_ENABLED'] = True
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Database configuration
DB_CONFIG = {
    'host': "localhost",
    'user': "root",
    'password': os.getenv("SECRET_KEY"),
    'database': "ticket_system"
}

ADMIN_PASSWORD = "admin123"

# ---- Form Class ---- #
class TicketForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired()])
    account = StringField('Account', 
        render_kw={
            "type": "text",  # Changed from number to text
            "inputmode": "numeric",
            "pattern": "[0-9]*",
            "minlength": "10",
            "maxlength": "10",
            "style": "width: 100%; -moz-appearance: textfield;",  # Removes spinner buttons
            "oninput": "this.value = this.value.replace(/[^0-9]/g, '')"  # Only allows digits
        },
        validators=[
            DataRequired(),
            Length(min=10, max=10, message="Account number must be exactly 10 digits."),
            Regexp('^[0-9]{10}$', message="Account number must contain only digits.")
        ])
    email = StringField('Email', validators=[DataRequired(), Email()])
    reference = StringField('Reference')
    error_type = SelectField('Error Type', choices=[
        ('', 'Select Error Type'),
        ('payment_failed', 'Payment Failed'),
        ('wrong_deduction', 'Wrong Deduction'),
        ('not_credited', 'Not Credited'),
        ('bank_one_loading', 'BankOne Issue'),
        ('other', 'Other'),
    ], validators=[DataRequired()])
    description = TextAreaField('Description', validators=[DataRequired()])
    file = FileField('Upload Screenshot (Optional)', validators=[
        FileAllowed(['jpg', 'png', 'pdf'], 'Only images and PDFs are allowed.')
    ])

# ---- Routes ---- #
@app.route('/', methods=['GET', 'POST'])
def form_view():
    form = TicketForm()
    if form.validate_on_submit():
        ticket_id = f"TICKET-{str(uuid.uuid4())[:8]}"
        filename = None
        uploaded_file = form.file.data

        if uploaded_file is not None and hasattr(uploaded_file, 'filename') and uploaded_file.filename:
            secured_name = secure_filename(uploaded_file.filename)
            filename = f"{ticket_id}_{secured_name}"
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            try:
                uploaded_file.save(file_path)
                print("File received:", uploaded_file.filename)
            except Exception as e:
                flash(f"File upload error: {str(e)}")
                return render_template('index.html', form=form)

        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor()
            sql = ("INSERT INTO tickets (ticket_id, fullname, account_number, email, reference, error_type, description, file_path, status, created_at) "
                   "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())")
            cursor.execute(sql, (
                ticket_id,
                form.name.data,
                int(form.account.data),
                form.email.data,
                form.reference.data,
                form.error_type.data,
                form.description.data,
                filename,
                "Open"
            ))
            conn.commit()
            cursor.close()
            conn.close()
            flash(f"Ticket {ticket_id} submitted successfully.")

            # Notification email
            msg = Message(
                subject=f"New Ticket Submitted: {ticket_id}",
                sender=app.config['MAIL_USERNAME'],
                recipients=[app.config['MAIL_USERNAME']]
            )
            msg.body = (
                f"Hello Admin,\n\n"
                f"A new ticket has been submitted with the following details:\n\n"
                f"Ticket ID: {ticket_id}\n"
                f"Submitted By: {form.name.data}\n"
                f"Account: {form.account.data}\n"
                f"Email: {form.email.data}\n"
                f"Transaction Reference: {form.reference.data}\n"
                f"Error Type: {form.error_type.data}\n"
                f"Description: {form.description.data}\n\n"
                f"View all tickets: {url_for('view_tickets', _external=True)}\n\n"
                f"Regards,\nDroid"
            )
            mail.send(msg)
            # Clear form on successful submission by redirecting (new GET request gives a brand-new form)
            return redirect('/')
        except Exception as e:
            flash(f"Database error: {str(e)}")
            return render_template('index.html', form=form)
    # Always return a response (for GET or when form validation fails)
    return render_template('index.html', form=form)

@app.route('/tickets', methods=['GET', 'POST'])
def view_tickets():
    # If already logged in, skip password input
    if session.get('admin_authenticated'):
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM tickets ORDER BY CASE WHEN status='Open' THEN 0 ELSE 1 END, created_at DESC")
            tickets = cursor.fetchall()
            cursor.close()
            conn.close()
        except Exception as e:
            flash(f"Database error: {str(e)}")
            tickets = []
        return render_template('tickets.html', tickets=tickets)
    
    # Otherwise, handle the admin login form
    if request.method == 'POST':
        password = request.form.get('password')
        if password == ADMIN_PASSWORD:
            # Set session flag so admin stays logged in
            session['admin_authenticated'] = True
            return redirect('/tickets')
        else:
            flash("Incorrect admin password.")
            return redirect('/tickets')
    return render_template('admin_login.html')

@app.route('/close_ticket/<ticket_id>', methods=['POST'])
def close_ticket(ticket_id):
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT email, fullname FROM tickets WHERE ticket_id = %s", (ticket_id,))
        ticket_info = cursor.fetchone()
        if not ticket_info:
            flash("Ticket not found.")
            cursor.close()
            conn.close()
            return redirect('/tickets')
        # Update to include closed_at timestamp
        update_sql = "UPDATE tickets SET status = 'Closed', closed_at = NOW() WHERE ticket_id = %s AND status = 'Open'"
        cursor.execute(update_sql, (ticket_id,))
        if cursor.rowcount == 0:
            flash("Ticket not found or already closed.")
        else:
            conn.commit()
            flash(f"Ticket {ticket_id} closed successfully.")
            msg = Message(subject="Ticket Closed Notification",
                          sender=app.config['MAIL_USERNAME'],
                          recipients=[ticket_info['email']])
            msg.body = (f"Hello {ticket_info['fullname']},\n\n"
                        f"Your ticket ({ticket_id}) has been closed.\n\nThank you.")
            mail.send(msg)
        cursor.close()
        conn.close()
    except Exception as e:
        flash(f"Database error: {str(e)}")
    return redirect('/tickets')

@app.route('/delete_ticket/<ticket_id>', methods=['POST'])
def delete_ticket(ticket_id):
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tickets WHERE ticket_id = %s", (ticket_id,))
        conn.commit()
        cursor.close()
        conn.close()
        flash(f"Ticket {ticket_id} deleted successfully.")
    except Exception as e:
        flash(f"Database error: {str(e)}")
    return redirect('/tickets')

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/ticket/<ticket_id>')
def ticket_detail(ticket_id):
    if not session.get('admin_authenticated'):
        return redirect('/tickets')
    
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM tickets WHERE ticket_id = %s", (ticket_id,))
        ticket = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if ticket:
            return render_template('ticket_detail.html', ticket=ticket)
        else:
            flash("Ticket not found.")
            return redirect('/tickets')
    except Exception as e:
        flash(f"Database error: {str(e)}")
        return redirect('/tickets')

@app.after_request
def set_secure_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'"
    return response

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug = True, threaded=False,)
