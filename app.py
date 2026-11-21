from datetime import datetime
import os
import re
from authlib.integrations.flask_client import OAuth
import pandas as pd
from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

try:
  import pypdf

  PYPDF_DISPONIBLE = True
except ImportError:
  PYPDF_DISPONIBLE = False

app = Flask(__name__)
# Clave secreta cargada de forma segura por variable de entorno (con respaldo para desarrollo local)
app.secret_key = os.environ.get('SECRET_KEY', 'clave_secreta_dinevia_super_segura_2026')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = None

MESES_NOMBRES = [
    'Enero',
    'Febrero',
    'Marzo',
    'Abril',
    'Mayo',
    'Junio',
    'Julio',
    'Agosto',
    'Septiembre',
    'Octubre',
    'Noviembre',
    'Diciembre',
]


def _parse_float(value, default=None):
  if value is None or str(value).strip() == '':
    return default
  try:
    return float(str(value).replace(',', '.').strip())
  except (ValueError, TypeError):
    return default


def _obtener_mes_anio_desde_fecha(fecha_str):
  fecha_dt = datetime.strptime(fecha_str, '%Y-%m-%d')
  return MESES_NOMBRES[fecha_dt.month - 1], str(fecha_dt.year)


# ==========================================
# CONFIGURACIÓN DE OAUTH (GOOGLE) SEGURA
# ==========================================
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID'),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
    server_metadata_url=(
        'https://accounts.google.com/.well-known/openid-configuration'
    ),
    client_kwargs={'scope': 'openid email profile'},
)


# ==========================================
# MODELOS DE BASE DE DATOS MULTIUSUARIO
# ==========================================
class User(UserMixin, db.Model):
  id = db.Column(db.Integer, primary_key=True)
  email = db.Column(db.String(150), unique=True, nullable=False)
  name = db.Column(db.String(150), nullable=False)
  password = db.Column(db.String(200), nullable=False)


class Movimiento(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
  fecha = db.Column(db.String(50), nullable=False)
  tipo = db.Column(db.String(50), nullable=False)
  monto = db.Column(db.Float, nullable=False)
  concepto = db.Column(db.String(200), nullable=True)
  categoria = db.Column(db.String(100), nullable=True)
  metodo = db.Column(db.String(100), nullable=True)
  mes = db.Column(db.String(50), nullable=False)
  anio = db.Column(db.String(50), nullable=False)


class Presupuesto(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
  mes = db.Column(db.String(50), nullable=False)
  anio = db.Column(db.String(50), nullable=False)
  categoria = db.Column(db.String(100), nullable=False)
  limite = db.Column(db.Float, nullable=False)


class Deuda(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
  descripcion = db.Column(db.String(200), nullable=False)
  monto_inicial = db.Column(db.Float, nullable=False)
  monto = db.Column(db.Float, nullable=False)


class AbonoDeuda(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
  deuda_id = db.Column(db.Integer, db.ForeignKey('deuda.id'), nullable=False)
  fecha = db.Column(db.String(50), nullable=False)
  monto = db.Column(db.Float, nullable=False)


class CategoriaIngreso(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
  categoria = db.Column(db.String(100), nullable=False)


class Nota(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
  titulo = db.Column(db.String(200), nullable=True)
  contenido = db.Column(db.Text, nullable=True)
  etiquetas = db.Column(db.String(200), nullable=True)
  fecha = db.Column(db.String(50), nullable=True)
  es_checklist = db.Column(db.Boolean, default=False, nullable=False)


class CuentaAhorro(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
  nombre_cuenta = db.Column(db.String(150), nullable=False)
  saldo_actual = db.Column(db.Float, nullable=False)


class MetaAhorro(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
  nombre_meta = db.Column(db.String(150), nullable=False)
  monto_objetivo = db.Column(db.Float, nullable=False)
  monto_actual = db.Column(db.Float, nullable=False, default=0.0)
  fecha_limite = db.Column(db.String(50), nullable=True)


class AbonoMeta(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
  meta_id = db.Column(
      db.Integer, db.ForeignKey('meta_ahorro.id'), nullable=False
  )
  fecha = db.Column(db.String(50), nullable=False)
  monto = db.Column(db.Float, nullable=False)


@login_manager.user_loader
def load_user(user_id):
  return User.query.get(int(user_id))


# ==========================================
# EXTRACCIÓN DE FACTURAS
# ==========================================
def extraer_monto_de_texto(texto):
  if not texto:
    return None
  keywords = [
      'VALOR TOTAL',
      'IMPORTE TOTAL',
      'TOTAL A PAGAR',
      'NETO A PAGAR',
      'TOTAL',
      'MONTO',
  ]
  for kw in keywords:
    patron = rf'{kw}[^\d]*(\d[\d.,]*)'
    matches = re.findall(patron, texto, re.IGNORECASE)
    for m in matches:
      val_str = m.strip()
      val_str = re.sub(r'[.,]+\$', '', val_str)
      if not val_str:
        continue
      if ',' in val_str and '.' in val_str:
        if val_str.rfind(',') > val_str.rfind('.'):
          val_str = val_str.replace('.', '').replace(',', '.')
        else:
          val_str = val_str.replace(',', '')
      elif ',' in val_str:
        val_str = val_str.replace(',', '.')
      try:
        num = float(val_str)
        if num > 0:
          return num
      except ValueError:
        continue
  return None


def procesar_archivo_factura(archivo):
  if archivo is None or archivo.filename == '':
    return None
  nombre = archivo.filename.lower()
  texto_extraido = ''
  if nombre.endswith('.pdf') and PYPDF_DISPONIBLE:
    try:
      reader = pypdf.PdfReader(archivo.stream)
      for pagina in reader.pages:
        t = pagina.extract_text()
        if t:
          texto_extraido += t + '\n'
    except Exception:
      pass
  elif nombre.endswith('.txt'):
    try:
      texto_extraido = archivo.read().decode('utf-8', errors='ignore')
    except Exception:
      pass
  elif nombre.endswith('.csv'):
    try:
      df_temp = pd.read_csv(archivo)
      texto_extraido = df_temp.to_string()
    except Exception:
      pass
  elif nombre.endswith('.xlsx'):
    try:
      df_temp = pd.read_excel(archivo)
      texto_extraido = df_temp.to_string()
    except Exception:
      pass
  return extraer_monto_de_texto(texto_extraido)


# ==========================================
# RUTAS DE AUTENTICACIÓN
# ==========================================
@app.route('/login', methods=['GET', 'POST'])
def login():
  if request.method == 'POST':
    email = (request.form.get('email') or '').strip().lower()
    password = request.form.get('password') or ''

    if not email or not password:
      flash('Completa tu correo electrónico y contraseña para iniciar sesión.', 'danger')
      return redirect(url_for('login'))

    user = User.query.filter_by(email=email).first()
    if user and check_password_hash(user.password, password):
      login_user(user)
      return redirect(url_for('dashboard'))

    flash('Correo o contraseña incorrectos. Inténtalo de nuevo.', 'danger')
    return redirect(url_for('login'))

  return render_template('login.html')


@app.route('/register', methods=['POST'])
def register():
  name = (request.form.get('name') or '').strip()
  email = (request.form.get('email') or '').strip().lower()
  password = request.form.get('password') or ''
  confirm_password = request.form.get('confirm_password') or ''

  if not name or not email or not password:
    flash('Completa todos los campos requeridos.', 'danger')
    return redirect(url_for('login'))

  if password != confirm_password:
    flash('Las contraseñas no coinciden. Por favor, revísalas.', 'danger')
    return redirect(url_for('login'))

  if User.query.filter_by(email=email).first():
    flash('Este correo ya se encuentra registrado.', 'danger')
    return redirect(url_for('login'))

  hashed_password = generate_password_hash(password)
  new_user = User(name=name, email=email, password=hashed_password)
  db.session.add(new_user)
  db.session.commit()
  login_user(new_user)
  return redirect(url_for('dashboard'))


# ==========================================
# RUTAS DE GOOGLE OAUTH
# ==========================================
@app.route('/login/google')
def login_google():
  redirect_uri = url_for('authorize_google', _external=True)
  return google.authorize_redirect(redirect_uri)


@app.route('/login/google/authorized')
def authorize_google():
  try:
    google.authorize_access_token()
    resp = google.get('https://www.googleapis.com/oauth2/v3/userinfo')
    user_info = resp.json()

    email = (user_info.get('email') or '').strip().lower()
    name = user_info.get('name') or 'Usuario Google'

    if not email:
      flash('No se pudo obtener el correo electrónico de Google.', 'danger')
      return redirect(url_for('login'))

    user = User.query.filter_by(email=email).first()

    if not user:
      dummy_password = generate_password_hash(os.urandom(24).hex())
      user = User(name=name, email=email, password=dummy_password)
      db.session.add(user)
      db.session.commit()

    login_user(user)
    return redirect(url_for('dashboard'))

  except Exception:
    flash('Ocurrió un error al autenticar con Google.', 'danger')
    return redirect(url_for('login'))


# ==========================================
# RUTA PRINCIPAL Y GESTIÓN DE PESTAÑAS
# ==========================================
@app.route('/')
@login_required
def dashboard():
  mes_param = request.args.get('mes')
  mes_actual = mes_param if mes_param in MESES_NOMBRES else MESES_NOMBRES[datetime.now().month - 1]
  anio_actual = request.args.get('anio', str(datetime.now().year))
  active_tab = request.args.get('tab', 'tab-dashboard')

  movimientos = Movimiento.query.filter_by(
      user_id=current_user.id, mes=mes_actual, anio=anio_actual
  ).all()
  total_ingresos = sum(m.monto for m in movimientos if m.tipo == 'Ingreso')
  total_gastos = sum(m.monto for m in movimientos if m.tipo == 'Gasto')

  idx_mes = MESES_NOMBRES.index(mes_actual)
  if idx_mes == 0:
    mes_anterior = 'Diciembre'
    anio_anterior = str(int(anio_actual) - 1)
  else:
    mes_anterior = MESES_NOMBRES[idx_mes - 1]
    anio_anterior = anio_actual

  movs_ant = Movimiento.query.filter_by(
      user_id=current_user.id, mes=mes_anterior, anio=anio_anterior
  ).all()
  ing_ant = sum(m.monto for m in movs_ant if m.tipo == 'Ingreso')
  gas_ant = sum(m.monto for m in movs_ant if m.tipo == 'Gasto')
  saldo_anterior = ing_ant - gas_ant

  incluir_remanente = request.args.get('remanente') == 'on'
  if incluir_remanente:
    saldo_neto = (total_ingresos + saldo_anterior) - total_gastos
  else:
    saldo_neto = total_ingresos - total_gastos

  presupuestos = Presupuesto.query.filter_by(
      user_id=current_user.id, mes=mes_actual, anio=anio_actual
  ).all()
  todos_presupuestos = Presupuesto.query.filter_by(
      user_id=current_user.id
  ).all()
  nombres_presupuestos_historial = sorted(
      list(set(p.categoria for p in todos_presupuestos))
  )

  presupuestos_detallados = []
  movs_gastos_mes = [m for m in movimientos if m.tipo == 'Gasto']
  for p in presupuestos:
    gastado = sum(m.monto for m in movs_gastos_mes if m.categoria == p.categoria)
    saldo_disp = p.limite - gastado
    presupuestos_detallados.append({
        'id': p.id,
        'categoria': p.categoria,
        'limite': p.limite,
        'gastado': gastado,
        'saldo': saldo_disp,
    })

  deudas = Deuda.query.filter_by(user_id=current_user.id).all()
  abonos = AbonoDeuda.query.filter_by(user_id=current_user.id).all()

  deudas_detalladas = []
  for d in deudas:
    abonos_deuda = [ab for ab in abonos if ab.deuda_id == d.id]
    total_abonos = sum(ab.monto for ab in abonos_deuda)
    saldo_pendiente = d.monto_inicial - total_abonos
    deudas_detalladas.append({
        'id': d.id,
        'descripcion': d.descripcion,
        'monto_inicial': d.monto_inicial,
        'total_abonos': total_abonos,
        'saldo_pendiente': saldo_pendiente,
    })

  categorias_ingreso = CategoriaIngreso.query.filter_by(
      user_id=current_user.id
  ).all()
  todos_movimientos = Movimiento.query.filter_by(user_id=current_user.id).all()
  notas_usuario = (
      Nota.query.filter_by(user_id=current_user.id)
      .order_by(Nota.id.desc())
      .all()
  )
  nota_usuario = notas_usuario[0] if notas_usuario else None

  cuentas_ahorro = CuentaAhorro.query.filter_by(user_id=current_user.id).all()
  metas_ahorro = MetaAhorro.query.filter_by(user_id=current_user.id).all()
  abonos_metas = AbonoMeta.query.filter_by(user_id=current_user.id).all()

  return render_template(
      'dashboard.html',
      name=current_user.name,
      mes_actual=mes_actual,
      anio_actual=anio_actual,
      meses=MESES_NOMBRES,
      active_tab=active_tab,
      total_ingresos=total_ingresos,
      total_gastos=total_gastos,
      saldo_neto=saldo_neto,
      saldo_anterior=saldo_anterior,
      incluir_remanente=incluir_remanente,
      mes_anterior=mes_anterior,
      anio_anterior=anio_anterior,
      movimientos=movimientos,
      presupuestos=presupuestos,
      presupuestos_detallados=presupuestos_detallados,
      nombres_presupuestos_historial=nombres_presupuestos_historial,
      deudas=deudas,
      deudas_detalladas=deudas_detalladas,
      abonos=abonos,
      categorias_ingreso=categorias_ingreso,
      todos_movimientos=todos_movimientos,
      todos_presupuestos=todos_presupuestos,
      nota_usuario=nota_usuario,
      notas_usuario=notas_usuario,
      cuentas_ahorro=cuentas_ahorro,
      metas_ahorro=metas_ahorro,
      abonos_metas=abonos_metas,
  )


# ==========================================
# RUTAS DE MOVIMIENTOS
# ==========================================
@app.route('/agregar_movimiento', methods=['POST'])
@login_required
def agregar_movimiento():
  tab = request.form.get('tab', 'tab-dashboard')
  try:
    tipo = request.form.get('tipo') or 'Gasto'
    fecha_str = (request.form.get('fecha') or '').strip()
    concepto = (request.form.get('concepto') or '').strip()
    categoria = (request.form.get('categoria') or '').strip()
    metodo = (request.form.get('metodo') or 'Efectivo').strip()

    if not fecha_str:
      flash('Debes seleccionar una fecha válida para el movimiento.', 'danger')
      return redirect(url_for('dashboard', tab=tab))

    archivo = request.files.get('archivo_factura')
    monto_extraido = procesar_archivo_factura(archivo)
    monto_form = _parse_float(request.form.get('monto'))

    if monto_extraido and monto_extraido > 0:
      monto = monto_extraido
    elif monto_form is not None and monto_form > 0:
      monto = monto_form
    else:
      flash('El monto del movimiento debe ser mayor a 0.00 o venir en una factura válida.', 'danger')
      return redirect(url_for('dashboard', tab=tab))

    mes_str, anio_str = _obtener_mes_anio_desde_fecha(fecha_str)

    nuevo_mov = Movimiento(
        user_id=current_user.id,
        fecha=fecha_str,
        tipo=tipo,
        monto=monto,
        concepto=concepto,
        categoria=categoria,
        metodo=metodo,
        mes=mes_str,
        anio=anio_str,
    )
    db.session.add(nuevo_mov)
    db.session.commit()
    return redirect(url_for('dashboard', mes=mes_str, anio=anio_str, tab=tab))
  except Exception:
    db.session.rollback()
    flash('No se pudo registrar el movimiento. Verifica los datos ingresados.', 'danger')
    return redirect(url_for('dashboard', tab=tab))


@app.route('/editar_movimiento/<int:id>', methods=['POST'])
@login_required
def editar_movimiento(id):
  mov = Movimiento.query.filter_by(id=id, user_id=current_user.id).first()
  tab = request.form.get('tab', 'tab-dashboard')
  if not mov:
    flash('El movimiento seleccionado no existe o no está permitido.', 'danger')
    return redirect(url_for('dashboard', tab=tab))

  try:
    nueva_fecha = (request.form.get('fecha') or mov.fecha).strip()
    nuevo_monto = _parse_float(request.form.get('monto'))
    if nuevo_monto is None or nuevo_monto <= 0:
      flash('El monto debe ser mayor a 0.00 para guardar los cambios.', 'danger')
      return redirect(url_for('dashboard', mes=mov.mes, anio=mov.anio, tab=tab))

    mov.fecha = nueva_fecha
    mov.monto = nuevo_monto
    mov.concepto = (request.form.get('concepto') or '').strip()
    mov.categoria = (request.form.get('categoria') or mov.categoria or '').strip()
    mov.metodo = (request.form.get('metodo') or mov.metodo or 'Efectivo').strip()
    mov.mes, mov.anio = _obtener_mes_anio_desde_fecha(nueva_fecha)

    db.session.commit()
    return redirect(url_for('dashboard', mes=mov.mes, anio=mov.anio, tab=tab))
  except Exception:
    db.session.rollback()
    flash('No se pudo actualizar el movimiento.', 'danger')
    return redirect(url_for('dashboard', mes=mov.mes, anio=mov.anio, tab=tab))


@app.route('/eliminar_movimiento/<int:id>', methods=['POST'])
@login_required
def eliminar_movimiento(id):
  mov = Movimiento.query.filter_by(id=id, user_id=current_user.id).first()
  tab = request.form.get('tab', 'tab-dashboard')
  if mov:
    try:
      mes, anio = mov.mes, mov.anio
      db.session.delete(mov)
      db.session.commit()
      return redirect(url_for('dashboard', mes=mes, anio=anio, tab=tab))
    except Exception:
      db.session.rollback()
      flash('No se pudo eliminar el movimiento.', 'danger')
  else:
    flash('El movimiento seleccionado no existe o no está permitido.', 'danger')
  return redirect(url_for('dashboard', tab=tab))


# ==========================================
# RUTAS DE PRESUPUESTOS
# ==========================================
@app.route('/agregar_presupuesto', methods=['POST'])
@login_required
def agregar_presupuesto():
  mes = request.form.get('mes') or MESES_NOMBRES[datetime.now().month - 1]
  anio = request.form.get('anio') or str(datetime.now().year)
  try:
    categoria = (request.form.get('categoria') or '').strip()
    limite = _parse_float(request.form.get('limite'))
    if not categoria or limite is None or limite <= 0:
      flash('Ingresa un nombre de presupuesto y un límite mayor a 0.00.', 'danger')
      return redirect(
          url_for('dashboard', mes=mes, anio=anio, tab='tab-presupuestos')
      )

    pres = Presupuesto.query.filter_by(
        user_id=current_user.id, mes=mes, anio=anio, categoria=categoria
    ).first()
    if pres:
      pres.limite = limite
    else:
      nuevo_p = Presupuesto(
          user_id=current_user.id,
          mes=mes,
          anio=anio,
          categoria=categoria,
          limite=limite,
      )
      db.session.add(nuevo_p)
    db.session.commit()
  except Exception:
    db.session.rollback()
    flash('No se pudo guardar el presupuesto.', 'danger')
  return redirect(
      url_for('dashboard', mes=mes, anio=anio, tab='tab-presupuestos')
  )


@app.route('/editar_presupuesto/<int:id>', methods=['POST'])
@login_required
def editar_presupuesto(id):
  pres = Presupuesto.query.filter_by(id=id, user_id=current_user.id).first()
  if not pres:
    flash('El presupuesto seleccionado no existe o no está permitido.', 'danger')
    return redirect(url_for('dashboard', tab='tab-presupuestos'))

  try:
    categoria = (request.form.get('categoria') or '').strip()
    limite = _parse_float(request.form.get('limite'))
    if not categoria or limite is None or limite <= 0:
      flash('El nombre del presupuesto es obligatorio y el límite debe ser mayor a 0.00.', 'danger')
      return redirect(
          url_for('dashboard', mes=pres.mes, anio=pres.anio, tab='tab-presupuestos')
      )

    pres.categoria = categoria
    pres.limite = limite
    db.session.commit()
    return redirect(
        url_for('dashboard', mes=pres.mes, anio=pres.anio, tab='tab-presupuestos')
    )
  except Exception:
    db.session.rollback()
    flash('No se pudo actualizar el presupuesto.', 'danger')
    return redirect(
        url_for('dashboard', mes=pres.mes, anio=pres.anio, tab='tab-presupuestos')
    )


@app.route('/eliminar_presupuesto/<int:id>', methods=['POST'])
@login_required
def eliminar_presupuesto(id):
  pres = Presupuesto.query.filter_by(id=id, user_id=current_user.id).first()
  if pres:
    try:
      mes, anio = pres.mes, pres.anio
      db.session.delete(pres)
      db.session.commit()
      return redirect(
          url_for('dashboard', mes=mes, anio=anio, tab='tab-presupuestos')
      )
    except Exception:
      db.session.rollback()
      flash('No se pudo eliminar el presupuesto.', 'danger')
  else:
    flash('El presupuesto seleccionado no existe o no está permitido.', 'danger')
  return redirect(url_for('dashboard', tab='tab-presupuestos'))


# ==========================================
# RUTAS DE DEUDAS Y ABONOS
# ==========================================
@app.route('/agregar_deuda', methods=['POST'])
@login_required
def agregar_deuda():
  try:
    descripcion = (request.form.get('descripcion') or '').strip()
    monto = _parse_float(request.form.get('monto'))
    if descripcion and monto is not None and monto > 0:
      nueva_d = Deuda(
          user_id=current_user.id,
          descripcion=descripcion,
          monto_inicial=monto,
          monto=monto,
      )
      db.session.add(nueva_d)
      db.session.commit()
    else:
      flash('Ingresa una descripción y un monto inicial mayor a 0.00 para la deuda.', 'danger')
  except Exception:
    db.session.rollback()
    flash('No se pudo registrar la deuda.', 'danger')
  return redirect(url_for('dashboard', tab='tab-deudas'))


@app.route('/editar_deuda/<int:id>', methods=['POST'])
@login_required
def editar_deuda(id):
  deuda = Deuda.query.filter_by(id=id, user_id=current_user.id).first()
  if deuda:
    try:
      descripcion = (request.form.get('descripcion') or '').strip()
      monto_inicial = _parse_float(request.form.get('monto_inicial'))
      if descripcion and monto_inicial is not None and monto_inicial > 0:
        deuda.descripcion = descripcion
        deuda.monto_inicial = monto_inicial
        db.session.commit()
      else:
        flash('La descripción es obligatoria y el monto inicial debe ser mayor a 0.00.', 'danger')
    except Exception:
      db.session.rollback()
      flash('No se pudo actualizar la deuda.', 'danger')
  else:
    flash('La deuda seleccionada no existe o no está permitido.', 'danger')
  return redirect(url_for('dashboard', tab='tab-deudas'))


@app.route('/eliminar_deuda/<int:id>', methods=['POST'])
@login_required
def eliminar_deuda(id):
  deuda = Deuda.query.filter_by(id=id, user_id=current_user.id).first()
  if deuda:
    try:
      AbonoDeuda.query.filter_by(deuda_id=id, user_id=current_user.id).delete()
      db.session.delete(deuda)
      db.session.commit()
    except Exception:
      db.session.rollback()
      flash('No se pudo eliminar la deuda.', 'danger')
  else:
    flash('La deuda seleccionada no existe o no está permitido.', 'danger')
  return redirect(url_for('dashboard', tab='tab-deudas'))


@app.route('/agregar_abono', methods=['POST'])
@login_required
def agregar_abono():
  try:
    deuda_id = int(request.form.get('deuda_id'))
    monto = _parse_float(request.form.get('monto'))
    fecha = (request.form.get('fecha') or '').strip()

    deuda = Deuda.query.filter_by(id=deuda_id, user_id=current_user.id).first()
    if deuda and monto is not None and monto > 0 and fecha:
      nuevo_ab = AbonoDeuda(
          user_id=current_user.id, deuda_id=deuda_id, fecha=fecha, monto=monto
      )
      db.session.add(nuevo_ab)
      db.session.commit()
    else:
      flash('Selecciona una deuda válida, una fecha y un monto mayor a 0.00 para el abono.', 'danger')
  except Exception:
    db.session.rollback()
    flash('No se pudo registrar el abono.', 'danger')
  return redirect(url_for('dashboard', tab='tab-deudas'))


@app.route('/editar_abono/<int:id>', methods=['POST'])
@login_required
def editar_abono(id):
  abono = AbonoDeuda.query.filter_by(id=id, user_id=current_user.id).first()
  if abono:
    try:
      monto = _parse_float(request.form.get('monto'))
      fecha = (request.form.get('fecha') or '').strip()
      deuda_id = int(request.form.get('deuda_id'))
      deuda_valida = Deuda.query.filter_by(
          id=deuda_id, user_id=current_user.id
      ).first()
      if deuda_valida and monto is not None and monto > 0 and fecha:
        abono.monto = monto
        abono.fecha = fecha
        abono.deuda_id = deuda_id
        db.session.commit()
      else:
        flash('Verifica la deuda asociada, la fecha y un monto mayor a 0.00 para el abono.', 'danger')
    except Exception:
      db.session.rollback()
      flash('No se pudo actualizar el abono.', 'danger')
  else:
    flash('El abono seleccionado no existe o no está permitido.', 'danger')
  return redirect(url_for('dashboard', tab='tab-deudas'))


@app.route('/eliminar_abono/<int:id>', methods=['POST'])
@login_required
def eliminar_abono(id):
  abono = AbonoDeuda.query.filter_by(id=id, user_id=current_user.id).first()
  if abono:
    try:
      db.session.delete(abono)
      db.session.commit()
    except Exception:
      db.session.rollback()
      flash('No se pudo eliminar el abono.', 'danger')
  else:
    flash('El abono seleccionado no existe o no está permitido.', 'danger')
  return redirect(url_for('dashboard', tab='tab-deudas'))


# ==========================================
# RUTAS DE CATEGORÍAS DE INGRESOS
# ==========================================
@app.route('/agregar_categoria_ingreso', methods=['POST'])
@login_required
def agregar_categoria_ingreso():
  categoria = (request.form.get('categoria') or '').strip()
  if categoria:
    try:
      existe = CategoriaIngreso.query.filter_by(
          user_id=current_user.id, categoria=categoria
      ).first()
      if not existe:
        nueva_c = CategoriaIngreso(
            user_id=current_user.id, categoria=categoria
        )
        db.session.add(nueva_c)
        db.session.commit()
      else:
        flash('Esa categoría de ingreso ya existe.', 'danger')
    except Exception:
      db.session.rollback()
      flash('No se pudo añadir la categoría de ingreso.', 'danger')
  else:
    flash('Ingresa un nombre válido para la categoría de ingreso.', 'danger')
  return redirect(url_for('dashboard', tab='tab-ingresos'))


@app.route('/eliminar_categoria_ingreso', methods=['POST'])
@login_required
def eliminar_categoria_ingreso():
  categoria = (request.form.get('categoria') or '').strip()
  if categoria:
    try:
      cat = CategoriaIngreso.query.filter_by(
          user_id=current_user.id, categoria=categoria
      ).first()
      if cat:
        db.session.delete(cat)
        db.session.commit()
      else:
        flash('La categoría seleccionada no existe.', 'danger')
    except Exception:
      db.session.rollback()
      flash('No se pudo borrar la categoría de ingreso.', 'danger')
  else:
    flash('Selecciona una categoría de ingreso para eliminar.', 'danger')
  return redirect(url_for('dashboard', tab='tab-ingresos'))


# ==========================================
# RUTAS DE GESTIÓN DE AHORROS
# ==========================================
@app.route('/agregar_cuenta_ahorro', methods=['POST'])
@login_required
def agregar_cuenta_ahorro():
  try:
    nombre_cuenta = (request.form.get('nombre_cuenta') or '').strip()
    saldo_actual = _parse_float(request.form.get('saldo_actual'), 0.0)
    if nombre_cuenta and saldo_actual is not None and saldo_actual >= 0:
      nueva_cuenta = CuentaAhorro(
          user_id=current_user.id,
          nombre_cuenta=nombre_cuenta,
          saldo_actual=saldo_actual,
      )
      db.session.add(nueva_cuenta)
      db.session.commit()
    else:
      flash('Ingresa un nombre de cuenta y un saldo actual igual o mayor a 0.00.', 'danger')
  except Exception:
    db.session.rollback()
    flash('No se pudo guardar la cuenta de ahorro.', 'danger')
  return redirect(url_for('dashboard', tab='tab-ahorros', active_section='ahorros'))


@app.route('/editar_cuenta_ahorro/<int:id>', methods=['POST'])
@login_required
def editar_cuenta_ahorro(id):
  try:
    cuenta = CuentaAhorro.query.filter_by(
        id=id, user_id=current_user.id
    ).first()
    nombre_cuenta = (request.form.get('nombre_cuenta') or '').strip()
    saldo_actual = _parse_float(request.form.get('saldo_actual'), 0.0)
    if cuenta and nombre_cuenta and saldo_actual is not None and saldo_actual >= 0:
      cuenta.nombre_cuenta = nombre_cuenta
      cuenta.saldo_actual = saldo_actual
      db.session.commit()
    else:
      flash('Selecciona una cuenta válida y verifica que el nombre y el saldo no sean inválidos.', 'danger')
  except Exception:
    db.session.rollback()
    flash('No se pudo actualizar la cuenta de ahorro.', 'danger')
  return redirect(url_for('dashboard', tab='tab-ahorros', active_section='ahorros'))


@app.route('/eliminar_cuenta_ahorro/<int:id>', methods=['POST'])
@login_required
def eliminar_cuenta_ahorro(id):
  try:
    cuenta = CuentaAhorro.query.filter_by(
        id=id, user_id=current_user.id
    ).first()
    if cuenta:
      db.session.delete(cuenta)
      db.session.commit()
    else:
      flash('La cuenta de ahorro seleccionada no existe o no está permitida.', 'danger')
  except Exception:
    db.session.rollback()
    flash('No se pudo eliminar la cuenta de ahorro.', 'danger')
  return redirect(url_for('dashboard', tab='tab-ahorros', active_section='ahorros'))


@app.route('/agregar_meta_ahorro', methods=['POST'])
@login_required
def agregar_meta_ahorro():
  try:
    nombre_meta = (request.form.get('nombre_meta') or '').strip()
    monto_objetivo = _parse_float(request.form.get('monto_objetivo'), 0.0)
    monto_actual = _parse_float(request.form.get('monto_actual'), 0.0)
    fecha_limite = (request.form.get('fecha_limite') or '').strip()
    if (
        nombre_meta
        and monto_objetivo is not None
        and monto_objetivo > 0
        and monto_actual is not None
        and monto_actual >= 0
    ):
      nueva_meta = MetaAhorro(
          user_id=current_user.id,
          nombre_meta=nombre_meta,
          monto_objetivo=monto_objetivo,
          monto_actual=monto_actual,
          fecha_limite=fecha_limite,
      )
      db.session.add(nueva_meta)
      db.session.commit()
    else:
      flash('Ingresa un nombre, un monto objetivo mayor a 0.00 y un monto inicial válido.', 'danger')
  except Exception:
    db.session.rollback()
    flash('No se pudo guardar la meta de ahorro.', 'danger')
  return redirect(url_for('dashboard', tab='tab-ahorros', active_section='ahorros'))


@app.route('/editar_meta_ahorro/<int:id>', methods=['POST'])
@login_required
def editar_meta_ahorro(id):
  try:
    meta = MetaAhorro.query.filter_by(id=id, user_id=current_user.id).first()
    nombre_meta = (request.form.get('nombre_meta') or '').strip()
    monto_objetivo = _parse_float(request.form.get('monto_objetivo'), 0.0)
    monto_actual = _parse_float(request.form.get('monto_actual'), 0.0)
    fecha_limite = (request.form.get('fecha_limite') or '').strip()
    if (
        meta
        and nombre_meta
        and monto_objetivo is not None
        and monto_objetivo > 0
        and monto_actual is not None
        and monto_actual >= 0
    ):
      meta.nombre_meta = nombre_meta
      meta.monto_objetivo = monto_objetivo
      meta.monto_actual = monto_actual
      meta.fecha_limite = fecha_limite
      db.session.commit()
    else:
      flash('Selecciona una meta válida y verifica que el objetivo sea mayor a 0.00.', 'danger')
  except Exception:
    db.session.rollback()
    flash('No se pudo actualizar la meta de ahorro.', 'danger')
  return redirect(url_for('dashboard', tab='tab-ahorros', active_section='ahorros'))


@app.route('/eliminar_meta_ahorro/<int:id>', methods=['POST'])
@login_required
def eliminar_meta_ahorro(id):
  try:
    meta = MetaAhorro.query.filter_by(id=id, user_id=current_user.id).first()
    if meta:
      AbonoMeta.query.filter_by(meta_id=id, user_id=current_user.id).delete()
      db.session.delete(meta)
      db.session.commit()
    else:
      flash('La meta de ahorro seleccionada no existe o no está permitida.', 'danger')
  except Exception:
    db.session.rollback()
    flash('No se pudo eliminar la meta de ahorro.', 'danger')
  return redirect(url_for('dashboard', tab='tab-ahorros', active_section='ahorros'))


@app.route('/agregar_abono_meta', methods=['POST'])
@login_required
def agregar_abono_meta():
  try:
    meta_id = int(request.form.get('meta_id'))
    monto = _parse_float(request.form.get('monto'), 0.0)
    fecha = (request.form.get('fecha') or '').strip()

    meta = MetaAhorro.query.filter_by(id=meta_id, user_id=current_user.id).first()
    if meta and monto is not None and monto > 0 and fecha:
      nuevo_abono = AbonoMeta(
          user_id=current_user.id, meta_id=meta_id, fecha=fecha, monto=monto
      )
      db.session.add(nuevo_abono)
      meta.monto_actual += monto
      db.session.commit()
    else:
      flash('Selecciona una meta válida, una fecha y un monto mayor a 0.00 para abonar.', 'danger')
  except Exception:
    db.session.rollback()
    flash('No se pudo registrar el abono a la meta.', 'danger')
  return redirect(url_for('dashboard', tab='tab-ahorros', active_section='ahorros'))


# ==========================================
# API ASÍNCRONA PARA NOTAS
# ==========================================
@app.route('/api/notas/crear', methods=['POST'])
@login_required
def api_crear_nota():
  try:
    data = request.get_json(silent=True) or {}
    titulo = (data.get('titulo') or '').strip()
    if not titulo:
      return (
          jsonify({'status': 'error', 'message': 'El título es obligatorio.'}),
          400,
      )

    contenido = data.get('contenido', '')
    etiquetas = (data.get('etiquetas') or '').strip()
    fecha = (data.get('fecha') or '').strip()
    es_checklist = bool(data.get('es_checklist', False))

    nueva_nota = Nota(
        user_id=current_user.id,
        titulo=titulo,
        contenido=contenido,
        etiquetas=etiquetas,
        fecha=fecha,
        es_checklist=es_checklist,
    )
    db.session.add(nueva_nota)
    db.session.commit()

    return (
        jsonify({
            'status': 'success',
            'nota': {
                'id': nueva_nota.id,
                'titulo': nueva_nota.titulo,
                'contenido': nueva_nota.contenido,
                'etiquetas': nueva_nota.etiquetas,
                'fecha': nueva_nota.fecha,
                'es_checklist': nueva_nota.es_checklist,
            },
        }),
        201,
    )
  except Exception as e:
    db.session.rollback()
    return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/notas/<int:id>', methods=['PUT', 'POST'])
@login_required
def api_actualizar_nota(id):
  try:
    nota = Nota.query.filter_by(id=id, user_id=current_user.id).first()
    if not nota:
      return (
          jsonify({'status': 'error', 'message': 'Nota no encontrada'}),
          404,
      )

    data = request.get_json(silent=True) or {}
    if 'titulo' in data:
      titulo_limpio = (data['titulo'] or '').strip()
      if not titulo_limpio:
        return (
            jsonify({'status': 'error', 'message': 'El título no puede estar vacío.'}),
            400,
        )
      nota.titulo = titulo_limpio
    if 'contenido' in data:
      nota.contenido = data['contenido']
    if 'etiquetas' in data:
      nota.etiquetas = (data['etiquetas'] or '').strip()
    if 'fecha' in data:
      nota.fecha = (data['fecha'] or '').strip()
    if 'es_checklist' in data:
      nota.es_checklist = bool(data['es_checklist'])

    db.session.commit()
    return (
        jsonify({
            'status': 'success',
            'nota': {
                'id': nota.id,
                'titulo': nota.titulo,
                'contenido': nota.contenido,
                'etiquetas': nota.etiquetas,
                'fecha': nota.fecha,
                'es_checklist': nota.es_checklist,
            },
        }),
        200,
    )
  except Exception as e:
    db.session.rollback()
    return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/notas/<int:id>', methods=['DELETE'])
@login_required
def api_eliminar_nota(id):
  try:
    nota = Nota.query.filter_by(id=id, user_id=current_user.id).first()
    if not nota:
      return (
          jsonify({'status': 'error', 'message': 'Nota no encontrada'}),
          404,
      )

    db.session.delete(nota)
    db.session.commit()
    return jsonify({'status': 'success', 'nota': {'id': id}}), 200
  except Exception as e:
    db.session.rollback()
    return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/guardar_nota', methods=['POST'])
@login_required
def api_guardar_nota():
  try:
    data = request.get_json(silent=True) or {}
    contenido = data.get('contenido', '')
    nota_id = data.get('id')

    if nota_id:
      nota = Nota.query.filter_by(id=nota_id, user_id=current_user.id).first()
      if nota:
        nota.contenido = contenido
      else:
        nota = Nota(
            user_id=current_user.id,
            contenido=contenido,
            titulo='Nota guardada',
        )
        db.session.add(nota)
    else:
      nota = Nota.query.filter_by(user_id=current_user.id).first()
      if nota:
        nota.contenido = contenido
      else:
        nota = Nota(
            user_id=current_user.id,
            contenido=contenido,
            titulo='Nota principal',
        )
        db.session.add(nota)

    db.session.commit()
    return (
        jsonify({
            'status': 'success',
            'nota': {
                'id': nota.id,
                'titulo': nota.titulo,
                'contenido': nota.contenido,
            },
        }),
        200,
    )
  except Exception as e:
    db.session.rollback()
    return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/logout')
@login_required
def logout():
  logout_user()
  return redirect(url_for('login'))


with app.app_context():
  db.create_all()

if __name__ == '__main__':
  app.run(debug=True, use_reloader=False)