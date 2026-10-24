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
    email = request.form.get('email')
    password = request.form.get('password')
    user = User.query.filter_by(email=email).first()
    if user and check_password_hash(user.password, password):
      login_user(user)
      return redirect(url_for('dashboard'))

    flash('Correo o contraseña incorrectos. Inténtalo de nuevo.', 'danger')
    return redirect(url_for('login'))

  return render_template('login.html')


@app.route('/register', methods=['POST'])
def register():
  name = request.form.get('name')
  email = request.form.get('email')
  password = request.form.get('password')
  confirm_password = request.form.get('confirm_password')

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
    token = google.authorize_access_token()
    resp = google.get('https://www.googleapis.com/oauth2/v3/userinfo')
    user_info = resp.json()

    email = user_info.get('email')
    name = user_info.get('name')

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

  except Exception as e:
    flash('Ocurrió un error al autenticar con Google.', 'danger')
    return redirect(url_for('login'))


# ==========================================
# RUTA PRINCIPAL Y GESTIÓN DE PESTAÑAS
# ==========================================
@app.route('/')
@login_required
def dashboard():
  meses_nombres = [
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
  mes_actual = request.args.get(
      'mes', meses_nombres[datetime.now().month - 1]
  )
  anio_actual = request.args.get('anio', str(datetime.now().year))
  active_tab = request.args.get('tab', 'tab-dashboard')

  movimientos = Movimiento.query.filter_by(
      user_id=current_user.id, mes=mes_actual, anio=anio_actual
  ).all()
  total_ingresos = sum(m.monto for m in movimientos if m.tipo == 'Ingreso')
  total_gastos = sum(m.monto for m in movimientos if m.tipo == 'Gasto')

  idx_mes = meses_nombres.index(mes_actual)
  if idx_mes == 0:
    mes_anterior = 'Diciembre'
    anio_anterior = str(int(anio_actual) - 1)
  else:
    mes_anterior = meses_nombres[idx_mes - 1]
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
  notas_usuario = Nota.query.filter_by(user_id=current_user.id).all()
  nota_usuario = notas_usuario[0] if notas_usuario else None

  cuentas_ahorro = CuentaAhorro.query.filter_by(user_id=current_user.id).all()
  metas_ahorro = MetaAhorro.query.filter_by(user_id=current_user.id).all()
  abonos_metas = AbonoMeta.query.filter_by(user_id=current_user.id).all()

  return render_template(
      'dashboard.html',
      name=current_user.name,
      mes_actual=mes_actual,
      anio_actual=anio_actual,
      meses=meses_nombres,
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
  tipo = request.form.get('tipo')
  fecha_str = request.form.get('fecha')
  concepto = request.form.get('concepto')
  categoria = request.form.get('categoria')
  metodo = request.form.get('metodo')
  tab = request.form.get('tab', 'tab-dashboard')

  archivo = request.files.get('archivo_factura')
  monto_extraido = procesar_archivo_factura(archivo)
  monto_form = request.form.get('monto')

  if monto_extraido and monto_extraido > 0:
    monto = monto_extraido
  elif monto_form:
    monto = float(monto_form)
  else:
    monto = 0.01

  fecha_dt = datetime.strptime(fecha_str, '%Y-%m-%d')
  meses_nombres = [
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
  mes_str = meses_nombres[fecha_dt.month - 1]
  anio_str = str(fecha_dt.year)

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


@app.route('/editar_movimiento/<int:id>', methods=['POST'])
@login_required
def editar_movimiento(id):
  mov = Movimiento.query.filter_by(id=id, user_id=current_user.id).first()
  tab = request.form.get('tab', 'tab-dashboard')
  if mov:
    mov.fecha = request.form.get('fecha')
    mov.monto = float(request.form.get('monto'))
    mov.concepto = request.form.get('concepto')
    mov.categoria = request.form.get('categoria')
    mov.metodo = request.form.get('metodo')

    fecha_dt = datetime.strptime(mov.fecha, '%Y-%m-%d')
    meses_nombres = [
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
    mov.mes = meses_nombres[fecha_dt.month - 1]
    mov.anio = str(fecha_dt.year)

    db.session.commit()
    return redirect(url_for('dashboard', mes=mov.mes, anio=mov.anio, tab=tab))
  return redirect(url_for('dashboard'))


@app.route('/eliminar_movimiento/<int:id>', methods=['POST'])
@login_required
def eliminar_movimiento(id):
  mov = Movimiento.query.filter_by(id=id, user_id=current_user.id).first()
  tab = request.form.get('tab', 'tab-dashboard')
  if mov:
    mes, anio = mov.mes, mov.anio
    db.session.delete(mov)
    db.session.commit()
    return redirect(url_for('dashboard', mes=mes, anio=anio, tab=tab))
  return redirect(url_for('dashboard'))


# ==========================================
# RUTAS DE PRESUPUESTOS
# ==========================================
@app.route('/agregar_presupuesto', methods=['POST'])
@login_required
def agregar_presupuesto():
  mes = request.form.get('mes')
  anio = request.form.get('anio')
  categoria = request.form.get('categoria')
  limite = float(request.form.get('limite'))

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
  return redirect(
      url_for('dashboard', mes=mes, anio=anio, tab='tab-presupuestos')
  )


@app.route('/editar_presupuesto/<int:id>', methods=['POST'])
@login_required
def editar_presupuesto(id):
  pres = Presupuesto.query.filter_by(id=id, user_id=current_user.id).first()
  if pres:
    pres.categoria = request.form.get('categoria')
    pres.limite = float(request.form.get('limite'))
    db.session.commit()
    return redirect(
        url_for('dashboard', mes=pres.mes, anio=pres.anio, tab='tab-presupuestos')
    )
  return redirect(url_for('dashboard', tab='tab-presupuestos'))


@app.route('/eliminar_presupuesto/<int:id>', methods=['POST'])
@login_required
def eliminar_presupuesto(id):
  pres = Presupuesto.query.filter_by(id=id, user_id=current_user.id).first()
  if pres:
    mes, anio = pres.mes, pres.anio
    db.session.delete(pres)
    db.session.commit()
    return redirect(
        url_for('dashboard', mes=mes, anio=anio, tab='tab-presupuestos')
    )
  return redirect(url_for('dashboard', tab='tab-presupuestos'))


# ==========================================
# RUTAS DE DEUDAS Y ABONOS
# ==========================================
@app.route('/agregar_deuda', methods=['POST'])
@login_required
def agregar_deuda():
  descripcion = request.form.get('descripcion')
  monto = float(request.form.get('monto'))
  nueva_d = Deuda(
      user_id=current_user.id,
      descripcion=descripcion,
      monto_inicial=monto,
      monto=monto,
  )
  db.session.add(nueva_d)
  db.session.commit()
  return redirect(url_for('dashboard', tab='tab-deudas'))


@app.route('/editar_deuda/<int:id>', methods=['POST'])
@login_required
def editar_deuda(id):
  deuda = Deuda.query.filter_by(id=id, user_id=current_user.id).first()
  if deuda:
    deuda.descripcion = request.form.get('descripcion')
    deuda.monto_inicial = float(request.form.get('monto_inicial'))
    db.session.commit()
  return redirect(url_for('dashboard', tab='tab-deudas'))


@app.route('/eliminar_deuda/<int:id>', methods=['POST'])
@login_required
def eliminar_deuda(id):
  deuda = Deuda.query.filter_by(id=id, user_id=current_user.id).first()
  if deuda:
    AbonoDeuda.query.filter_by(deuda_id=id).delete()
    db.session.delete(deuda)
    db.session.commit()
  return redirect(url_for('dashboard', tab='tab-deudas'))


@app.route('/agregar_abono', methods=['POST'])
@login_required
def agregar_abono():
  deuda_id = int(request.form.get('deuda_id'))
  monto = float(request.form.get('monto'))
  fecha = request.form.get('fecha')

  deuda = Deuda.query.filter_by(id=deuda_id, user_id=current_user.id).first()
  if deuda:
    nuevo_ab = AbonoDeuda(
        user_id=current_user.id, deuda_id=deuda_id, fecha=fecha, monto=monto
    )
    db.session.add(nuevo_ab)
    db.session.commit()
  return redirect(url_for('dashboard', tab='tab-deudas'))


@app.route('/editar_abono/<int:id>', methods=['POST'])
@login_required
def editar_abono(id):
  abono = AbonoDeuda.query.filter_by(id=id, user_id=current_user.id).first()
  if abono:
    abono.monto = float(request.form.get('monto'))
    abono.fecha = request.form.get('fecha')
    abono.deuda_id = int(request.form.get('deuda_id'))
    db.session.commit()
  return redirect(url_for('dashboard', tab='tab-deudas'))


@app.route('/eliminar_abono/<int:id>', methods=['POST'])
@login_required
def eliminar_abono(id):
  abono = AbonoDeuda.query.filter_by(id=id, user_id=current_user.id).first()
  if abono:
    db.session.delete(abono)
    db.session.commit()
  return redirect(url_for('dashboard', tab='tab-deudas'))


# ==========================================
# RUTAS DE CATEGORÍAS DE INGRESOS
# ==========================================
@app.route('/agregar_categoria_ingreso', methods=['POST'])
@login_required
def agregar_categoria_ingreso():
  categoria = request.form.get('categoria')
  if categoria:
    existe = CategoriaIngreso.query.filter_by(
        user_id=current_user.id, categoria=categoria
    ).first()
    if not existe:
      nueva_c = CategoriaIngreso(
          user_id=current_user.id, categoria=categoria
      )
      db.session.add(nueva_c)
      db.session.commit()
  return redirect(url_for('dashboard', tab='tab-ingresos'))


@app.route('/eliminar_categoria_ingreso', methods=['POST'])
@login_required
def eliminar_categoria_ingreso():
  categoria = request.form.get('categoria')
  if categoria:
    Cat = CategoriaIngreso.query.filter_by(
        user_id=current_user.id, categoria=categoria
    ).first()
    if Cat:
      db.session.delete(Cat)
      db.session.commit()
  return redirect(url_for('dashboard', tab='tab-ingresos'))


# ==========================================
# RUTAS DE GESTIÓN DE AHORROS
# ==========================================
@app.route('/agregar_cuenta_ahorro', methods=['POST'])
@login_required
def agregar_cuenta_ahorro():
  try:
    nombre_cuenta = request.form.get('nombre_cuenta')
    saldo_actual = float(request.form.get('saldo_actual', 0.0))
    nueva_cuenta = CuentaAhorro(
        user_id=current_user.id,
        nombre_cuenta=nombre_cuenta,
        saldo_actual=saldo_actual,
    )
    db.session.add(nueva_cuenta)
    db.session.commit()
  except Exception:
    db.session.rollback()
  return redirect(url_for('dashboard', tab='tab-ahorros', active_section='ahorros'))


@app.route('/editar_cuenta_ahorro/<int:id>', methods=['POST'])
@login_required
def editar_cuenta_ahorro(id):
  try:
    cuenta = CuentaAhorro.query.filter_by(
        id=id, user_id=current_user.id
    ).first()
    if cuenta:
      cuenta.nombre_cuenta = request.form.get('nombre_cuenta')
      cuenta.saldo_actual = float(request.form.get('saldo_actual', 0.0))
      db.session.commit()
  except Exception:
    db.session.rollback()
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
  except Exception:
    db.session.rollback()
  return redirect(url_for('dashboard', tab='tab-ahorros', active_section='ahorros'))


@app.route('/agregar_meta_ahorro', methods=['POST'])
@login_required
def agregar_meta_ahorro():
  try:
    nombre_meta = request.form.get('nombre_meta')
    monto_objetivo = float(request.form.get('monto_objetivo', 0.0))
    monto_actual = float(request.form.get('monto_actual', 0.0))
    fecha_limite = request.form.get('fecha_limite')
    nueva_meta = MetaAhorro(
        user_id=current_user.id,
        nombre_meta=nombre_meta,
        monto_objetivo=monto_objetivo,
        monto_actual=monto_actual,
        fecha_limite=fecha_limite,
    )
    db.session.add(nueva_meta)
    db.session.commit()
  except Exception:
    db.session.rollback()
  return redirect(url_for('dashboard', tab='tab-ahorros', active_section='ahorros'))


@app.route('/editar_meta_ahorro/<int:id>', methods=['POST'])
@login_required
def editar_meta_ahorro(id):
  try:
    meta = MetaAhorro.query.filter_by(id=id, user_id=current_user.id).first()
    if meta:
      meta.nombre_meta = request.form.get('nombre_meta')
      meta.monto_objetivo = float(request.form.get('monto_objetivo', 0.0))
      meta.monto_actual = float(request.form.get('monto_actual', 0.0))
      meta.fecha_limite = request.form.get('fecha_limite')
      db.session.commit()
  except Exception:
    db.session.rollback()
  return redirect(url_for('dashboard', tab='tab-ahorros', active_section='ahorros'))


@app.route('/eliminar_meta_ahorro/<int:id>', methods=['POST'])
@login_required
def eliminar_meta_ahorro(id):
  try:
    meta = MetaAhorro.query.filter_by(id=id, user_id=current_user.id).first()
    if meta:
      AbonoMeta.query.filter_by(meta_id=id).delete()
      db.session.delete(meta)
      db.session.commit()
  except Exception:
    db.session.rollback()
  return redirect(url_for('dashboard', tab='tab-ahorros', active_section='ahorros'))


@app.route('/agregar_abono_meta', methods=['POST'])
@login_required
def agregar_abono_meta():
  try:
    meta_id = int(request.form.get('meta_id'))
    monto = float(request.form.get('monto', 0.0))
    fecha = request.form.get('fecha')

    meta = MetaAhorro.query.filter_by(id=meta_id, user_id=current_user.id).first()
    if meta and monto > 0:
      nuevo_abono = AbonoMeta(
          user_id=current_user.id, meta_id=meta_id, fecha=fecha, monto=monto
      )
      db.session.add(nuevo_abono)
      meta.monto_actual += monto
      db.session.commit()
  except Exception:
    db.session.rollback()
  return redirect(url_for('dashboard', tab='tab-ahorros', active_section='ahorros'))


# ==========================================
# API ASÍNCRONA PARA NOTAS
# ==========================================
@app.route('/api/notas/crear', methods=['POST'])
@login_required
def api_crear_nota():
  try:
    data = request.get_json(silent=True) or {}
    titulo = data.get('titulo', 'Nueva nota')
    contenido = data.get('contenido', '')
    etiquetas = data.get('etiquetas', '')
    fecha = data.get('fecha', '')
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
      nota.titulo = data['titulo']
    if 'contenido' in data:
      nota.contenido = data['contenido']
    if 'etiquetas' in data:
      nota.etiquetas = data['etiquetas']
    if 'fecha' in data:
      nota.fecha = data['fecha']
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


if __name__ == '__main__':
  with app.app_context():
    db.create_all()
  app.run(debug=True, use_reloader=False)