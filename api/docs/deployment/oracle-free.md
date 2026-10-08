# Despliegue personal gratuito: Oracle Cloud + Vercel

Backend FastAPI, PostgreSQL y scraper en una sola máquina con Docker Compose.
La web Next.js se publica por separado en Vercel Hobby. La API exige una clave
privada desde el proxy; Vercel Authentication limita quién puede abrir la web.

## 1. Crear la cuenta y la instancia

Crear una cuenta en [Oracle Cloud Free Tier](https://www.oracle.com/cloud/free/).
La verificación normalmente requiere tarjeta y teléfono; no compartir esos
datos ni una clave SSH privada en el chat.

Elegir con cuidado la **home region**: no se cambia después y los recursos
Always Free deben estar allí. São Paulo es una opción cercana a Paraguay, si
está disponible al registrarse; cercanía no garantiza capacidad gratuita.

En **Compute → Instances → Create instance**, seleccionar:

| Campo | Valor para esta primera prueba |
| --- | --- |
| Imagen | Ubuntu 24.04 LTS ARM, marcada como Always Free eligible |
| Shape | `VM.Standard.A1.Flex` |
| CPU y memoria | 2 OCPU y hasta 12 GB RAM en total para la cuenta gratuita |
| Boot volume | 50 GB; incluye el sistema, base y documentos |
| Red | Subred pública, Internet Gateway y dirección IPv4 pública |
| SSH | Guardar la clave privada; subir únicamente la pública |

La documentación vigente consultada el 6/10/2026 indica 1.500 OCPU-h y 9.000
GB-h mensuales para A1; equivale a 2 OCPU/12 GB continuos. El almacenamiento
gratuito suma hasta 200 GB entre discos de arranque y datos. Si no hay capacidad,
probar otro availability domain de esa misma región o esperar. No elegir una
shape de pago para evitar el error. Oracle puede recuperar máquinas consideradas
inactivas; conservar backups descargados fuera del servidor.

[Límites oficiales](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm),
[registro](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm),
[home region](https://blogs.oracle.com/apex/getting-started-with-oracle-cloud-free-tier-always-free-services-including-oracle-autonomous-database).

## 2. SSH, puertos y hostname gratuito

Desde PowerShell, reemplazar las rutas y la IP por las de la instancia:

```powershell
ssh -i C:/ruta/a/clave-privada.key ubuntu@IP_PUBLICA
```

En la Security List o Network Security Group de Oracle permitir TCP 80 y 443
desde Internet. TCP 22 debe permitir la IP desde la que se administrará por SSH.
La API y PostgreSQL no publican puertos 8000/5432 en producción.

La imagen Ubuntu de Oracle puede tener reglas de firewall adicionales. Revisar
las reglas existentes y permitir 80/443 antes de cualquier REJECT; no borrar el
firewall. Seguir la [guía de red de Oracle para Ubuntu](https://blogs.oracle.com/developers/enabling-network-traffic-to-ubuntu-images-in-oracle-cloud-infrastructure).
Docker publica los puertos del proxy mediante sus reglas de red.

Para probar sin comprar un dominio, usar `banks.IP_PUBLICA.sslip.io`, por ejemplo
`banks.203.0.113.20.sslip.io` reemplazando esa IP ilustrativa por la real. Ese DNS
resuelve la IP incluida en el nombre; Caddy puede obtener y renovar HTTPS al
alcanzar el servidor por 80/443. Si ya existe un dominio, apuntar su registro A a
la instancia y usarlo en lugar de sslip.io.

sslip.io es un servicio externo gratuito: si la autoridad de certificados aplica
un límite compartido, revisar los logs y usar la alternativa indicada por el
proveedor. [DNS y certificados del servicio](https://nip.io/).

## 3. Instalar Docker y copiar el código

En la máquina Ubuntu, instalar Git/OpenSSL y seguir la
[instalación oficial de Docker Engine y el plugin Compose para Ubuntu](https://docs.docker.com/engine/install/ubuntu/).
Los comandos siguientes usan `sudo docker`; no requieren agregar usuarios al
grupo Docker. Después verificar:

```bash
sudo apt-get update
sudo apt-get install -y git openssl
sudo docker version
sudo docker compose version
```

Publicar los cambios en GitHub antes de clonar el repositorio en el servidor.
Desplegar una revisión identificable que contenga esta guía, el proxy y los
cambios de frontend. Crear commits locales no actualiza el repositorio remoto.

```bash
git clone https://github.com/mbutlerov/banks-discounts-py.git
cd banks-discounts-py
# Si los cambios están en una rama específica, seleccionarla antes de continuar.
```

## 4. Configurar los secretos del servidor

```bash
cp deploy.env.example deploy.env
chmod 600 deploy.env
openssl rand -hex 32
openssl rand -hex 32
# Una tercera generación permite habilitar administración HTTP, si se necesita.
openssl rand -hex 32
nano deploy.env
```

Usar generaciones distintas para `DB_PASSWORD`, `API_ACCESS_KEY` y, opcionalmente,
`ADMIN_API_KEY`. Completar también `API_DOMAIN` con el hostname sin protocolo ni
ruta y `TLS_EMAIL` con un correo propio. Las claves hexadecimales evitan problemas
de interpolación y comodines en el proxy. `deploy.env` está ignorado por Git.
No utilizar `api/environments/private.env` como configuración de producción:
ese archivo continúa sirviendo para desarrollo; producción utiliza `deploy.env`.

El password se utiliza tanto para inicializar PostgreSQL como para conectar API
y worker. Cambiarlo en el archivo no modifica automáticamente el password de una
base existente. Esta guía inicializa una base nueva; una base previa requiere
backup y rotación coordinada de credenciales.

## 5. Migrar y arrancar

Crear esta función en la terminal del servidor, dentro del repositorio:

```bash
dc() {
  sudo docker compose --env-file deploy.env -p banks-prod \
    -f docker-compose.yml -f docker-compose.prod.yml --profile scraping "$@"
}

dc config --quiet
dc build api scraper
dc up -d --wait db
dc run --rm --no-deps api alembic upgrade head
dc run --rm --no-deps api python -m app.database.seed
dc up -d --wait api
dc up -d proxy scraper
dc ps
dc logs --tail 40 proxy scraper
```

La compilación debe terminar correctamente en ARM antes de publicar. Las
migraciones y el seed se ejecutan antes de iniciar la API y el worker. El seed
crea catálogos, no copia promociones de desarrollo. El worker programado obtiene
las promociones desde las fuentes bancarias. Para revisar su avance:

```bash
dc logs --tail 80 scraper
```

Para ejecutar un banco manualmente, detener antes el worker programado y
restaurarlo al terminar; evita competir por los locks:

```bash
dc stop scraper
dc exec api python -m app.scraping.cli run --bank itau
dc start scraper
```

Una corrida parcial conserva los datos válidos y los pendientes. Si alguna
fuente bancaria rechaza la IP de la nube, revisar su corrida; la conectividad
local no demuestra que la fuente acepte todas las IP de servidores.

## 6. Verificar la API y publicar el frontend

Sin clave, `https://HOSTNAME/api/v1/catalogs/banks` debe responder **401**. Desde
una herramienta HTTP privada, agregar `X-API-Key` con `API_ACCESS_KEY`: catálogo,
promociones y health deben responder correctamente. No enviar la clave por URL.
Los endpoints administrativos también requieren `Authorization: Bearer` con su
clave `ADMIN_API_KEY` independiente. Puede administrarse por CLI/SSH sin habilitar
esa clave. El navegador no puede abrir Swagger a través del proxy sin el header
de acceso; la clave no está incorporada en la página Swagger.

En el repositorio frontend, preparar y subir sus commits a GitHub. Crear en
Vercel un proyecto **Hobby** desde ese repo, con framework Next.js y Node 22.
Configurar variables privadas:

```dotenv
API_BASE_URL=https://HOSTNAME/api/v1
API_ACCESS_KEY=LA_MISMA_CLAVE_DE_ACCESO_DEL_PROXY
```

No usar prefijos `NEXT_PUBLIC_` ni compartir `ADMIN_API_KEY` con la web. Next.js
envía `X-API-Key` exclusivamente desde el servidor y rechaza redirects de API.
Vercel Hobby corresponde al uso personal sin fines comerciales.

Para restringir la web al dueño: **Security → Deployment Protection → Vercel
Authentication → All Deployments**. Está disponible gratis para producción desde
el 9/9/2026. Comprobar en incógnito que pide acceso, y con la cuenta autorizada
que se renderizan bancos y promociones. Proteger Vercel no sustituye la clave de
la API.

[Hobby](https://vercel.com/docs/plans/hobby),
[protección gratuita de producción](https://vercel.com/changelog/protect-production-deployments-for-free-on-every-plan).
Consultar también el README del repositorio frontend para su configuración.

## 7. Backups y actualizaciones

Desde la raíz del backend en el servidor:

```bash
sudo sh scripts/backup-production.sh /var/backups/banks-discounts
```

El script guarda un dump PostgreSQL, un tar de `/app/data` y hashes SHA-256 juntos.
Pausa el scraper y sólo lo restaura si estaba corriendo; no ejecutar ingestas
manuales mientras se realiza el backup. Copiar la carpeta resultante fuera del
servidor. No utilizar `docker compose down -v`: elimina los datos persistentes.
En la carpeta completa, `sha256sum --check SHA256SUMS` verifica los archivos.
Antes de confiar en una rutina de backup, probar una restauración en una base
independiente. Evitar también modificaciones administrativas mientras se copia.

Antes de una actualización, ejecutar el backup y detener el scraper. Después
seleccionar la revisión ya validada por CI y actualizar dentro del repositorio:

```bash
dc stop scraper
dc build api scraper
dc run --rm --no-deps api alembic upgrade head
dc run --rm --no-deps api python -m app.database.seed
dc up -d --wait api
dc up -d proxy scraper
```

Desplegar todavía requiere acceso a la cuenta, una instancia con capacidad y
conocer su IP pública. Esta guía no presupone que esos recursos ya existan.

## Verificación local de esta preparación

Se construyó y ejecutó la imagen ARM64 mediante emulación, comprobando FastAPI,
conexión PostgreSQL, lectura/render de PDF y Tesseract en español. Un stack
aislado pasó migraciones y seed; su proxy rechazó claves ausentes/incorrectas y
permitió consultas válidas. Se verificó un backup real, los bytes del documento
y la restauración PostgreSQL en una base de pruebas independiente.

El frontend pasó tipos, lint, 11 unitarias, build y 15 pruebas de navegador,
incluyendo clave privada ausente del HTML/scripts y rechazo de redirects.
HTTPS con certificado público y acceso a bancos desde Oracle se comprueban al
disponer de la instancia real; el ensayo del proxy utilizó HTTP en loopback.
