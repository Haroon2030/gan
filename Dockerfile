FROM python:3.12-slim

# قاعدة SQLite والمفتاح السري داخل /app/data (مجلد دائم) — فيها المستخدمون
# وإعدادات الربط المُدخلة من الشاشة، فلا تضيع عند إعادة بناء الحاوية
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    DATA_DIR=/app/data \
    DATABASE_PATH=/app/data/db.sqlite3

WORKDIR /app

# Instant Client مطلوب لـ Thick mode (DPY-3015 مع كلمة سر أوراكل القديمة)
# الإصدار 19 لا 23: عميل 23 لا يقبل مصادق كلمة السر القديم (10G / 0x939)
# في قواعد أونكس على Oracle 11g فيرفض الدخول بـ ORA-01017 رغم صحة كلمة السر
ARG ORACLE_IC_ZIP_URL=https://download.oracle.com/otn_software/linux/instantclient/1928000/instantclient-basiclite-linux.x64-19.28.0.0.0dbru.zip

# sqlnet.ora يسمح ببروتوكول الدخول القديم (عميل 12c+ يمنعه افتراضياً)
ENV TNS_ADMIN=/opt/oracle/tns

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        unzip \
        ca-certificates \
        openssh-server \
    && (apt-get install -y --no-install-recommends libaio1t64 \
        || apt-get install -y --no-install-recommends libaio1) \
    && (ln -sf /usr/lib/x86_64-linux-gnu/libaio.so.1t64 /usr/lib/x86_64-linux-gnu/libaio.so.1 || true) \
    && mkdir -p /opt/oracle \
    && curl -fsSL -o /tmp/instantclient.zip "${ORACLE_IC_ZIP_URL}" \
    && unzip -q /tmp/instantclient.zip -d /opt/oracle \
    && rm -f /tmp/instantclient.zip \
    && IC_DIR="$(find /opt/oracle -maxdepth 1 -type d -name 'instantclient_*' | head -n 1)" \
    && test -n "${IC_DIR}" \
    && echo "${IC_DIR}" > /etc/ld.so.conf.d/oracle-instantclient.conf \
    && ldconfig \
    && mkdir -p "${TNS_ADMIN}" \
    && echo 'SQLNET.ALLOWED_LOGON_VERSION_CLIENT=8' > "${TNS_ADMIN}/sqlnet.ora" \
    && rm -f /etc/ssh/ssh_host_* \
    && useradd --no-create-home --shell /usr/sbin/nologin oralink \
    && usermod -p '*' oralink \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x /app/entrypoint.sh \
    && mkdir -p /app/data /app/staticfiles

# 2222 = وكيل الربط (نفق SSH عكسي من جهاز قاعدة العميل)
EXPOSE 8000 2222

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/" >/dev/null || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
