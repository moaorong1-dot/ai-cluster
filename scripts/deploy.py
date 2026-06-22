#!/usr/bin/env python3
"""Deploy StarTrack Brain (星轨智库) to AI cluster (134.175.175.142).

Deploys: backend (port 8792) + frontend (Nginx static) + systemd service
"""
import paramiko
import os
import sys
import time
import glob

HOST = "134.175.175.142"
PORT = 22
USER = "ubuntu"
PASS = "Mar123456789"

LOCAL_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # scripts/..
REMOTE_BASE = "/opt/startrack-brain"
NGINX_ROOT = "/usr/share/nginx/startrack-brain"

# Files to deploy
SOURCE_FILES = [
    "main.py",
    "config.py",
    "config.yaml",
    "requirements.txt",
]

ROUTE_FILES = [
    "routes/__init__.py",
    "routes/brain.py",
]

SERVICE_FILES = [
    "services/__init__.py",
    "services/brain_store.py",
    "services/brain_graph.py",
    "services/llm_client.py",
    "services/embedding_service.py",
]

MIDDLEWARE_FILES = [
    "middleware/__init__.py",
    "middleware/jwt_auth.py",
]

FRONTEND_FILES = [
    "frontend/index.html",
]

ALL_FILES = SOURCE_FILES + ROUTE_FILES + SERVICE_FILES + MIDDLEWARE_FILES + FRONTEND_FILES


def run_ssh(client, cmd, timeout=30):
    """Run command via SSH and return stdout, stderr."""
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    return stdout.read().decode(), stderr.read().decode()


def deploy():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        print(f"🚀 连接 {HOST}...")
        client.connect(hostname=HOST, port=PORT, username=USER, password=PASS, timeout=30)
        print("✅ SSH 已连接")

        sftp = client.open_sftp()

        # ── Step 1: Create remote directories ──
        print("\n📁 创建远程目录...")
        dirs = [
            REMOTE_BASE,
            f"{REMOTE_BASE}/routes",
            f"{REMOTE_BASE}/services",
            f"{REMOTE_BASE}/middleware",
            f"{REMOTE_BASE}/utils",
            f"{REMOTE_BASE}/data",
            f"{REMOTE_BASE}/frontend",
            NGINX_ROOT,
        ]
        for d in dirs:
            out, err = run_ssh(client, f"sudo mkdir -p '{d}' && sudo chown ubuntu:ubuntu '{d}'")
        print("✅ 目录已创建")

        # ── Step 2: Upload source files ──
        print("\n📤 上传源文件...")
        total_size = 0
        for rel_path in ALL_FILES:
            local_path = os.path.join(LOCAL_BASE, rel_path)
            remote_path = f"{REMOTE_BASE}/{rel_path}"

            if not os.path.exists(local_path):
                print(f"  ⚠️ 跳过 (本地不存在): {rel_path}")
                continue

            tmp_path = f"/tmp/brain_{os.path.basename(rel_path)}"
            sftp.put(local_path, tmp_path)
            size = os.path.getsize(local_path)
            total_size += size

            # Copy with sudo
            cmds = f"""
            [ -f '{remote_path}' ] && sudo cp '{remote_path}' '{remote_path}.bak'
            sudo cp '{tmp_path}' '{remote_path}'
            sudo chown root:root '{remote_path}'
            rm -f '{tmp_path}'
            echo 'DONE'
            """
            out, err = run_ssh(client, cmds)
            if "DONE" in out:
                print(f"  ✅ {rel_path} ({size}B)")

        print(f"✅ 总计上传 {total_size / 1024:.1f} KB")

        # ── Step 3: Install dependencies ──
        print("\n📦 安装 Python 依赖...")
        install_cmd = f"""
        cd '{REMOTE_BASE}'
        sudo /usr/bin/pip3.11 install -r requirements.txt --quiet 2>&1 | tail -5 || \
        sudo /usr/bin/pip3 install -r requirements.txt --quiet 2>&1 | tail -5
        echo 'INSTALL_DONE'
        """
        out, err = run_ssh(client, install_cmd, timeout=120)
        if "INSTALL_DONE" in out:
            print("✅ 依赖安装完成")
        else:
            print(f"  ⚠️ 安装输出: {out[-200:] if out else '无输出'}")

        # ── Step 4: Create .env from example ──
        print("\n🔐 检查环境变量配置...")
        env_check = run_ssh(client, f"test -f '{REMOTE_BASE}/.env' && echo 'EXISTS' || echo 'MISSING'")[0].strip()
        if "MISSING" in env_check:
            # Copy .env.example to .env
            run_ssh(client, f"sudo cp '{REMOTE_BASE}/.env.example' '{REMOTE_BASE}/.env' 2>/dev/null")
            print("  ⚠️ .env 文件已从模板创建，请在服务器上填入 API Key")
        else:
            print("  ✅ .env 已存在")

        # ── Step 5: Set up systemd service ──
        print("\n⚙️ 配置 systemd 服务...")
        service_content = f"""[Unit]
Description=StarTrack Brain (星轨智库) — AI Second Brain
After=network.target docker.service
Wants=network.target docker.service

[Service]
Type=simple
User=root
WorkingDirectory={REMOTE_BASE}
Environment="PYTHONUNBUFFERED=1"
Environment="BRAIN_PORT=8792"
ExecStart=/usr/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8792 --log-level info
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

        # Write service file via SSH
        write_service_cmd = f"""
        sudo tee /etc/systemd/system/startrack-brain.service > /dev/null << 'SERVICEEOF'
{service_content}
SERVICEEOF
        echo 'SERVICE_WRITTEN'
        """
        out, err = run_ssh(client, write_service_cmd)
        if "SERVICE_WRITTEN" in out:
            print("  ✅ systemd 服务文件已写入")

        # Enable and start
        out, err = run_ssh(client, "sudo systemctl daemon-reload && sudo systemctl enable startrack-brain 2>&1")
        print(f"  📋 systemctl: {out.strip()}")
        out, err = run_ssh(client, "sudo systemctl restart startrack-brain 2>&1")
        print(f"  🔄 重启: {out.strip()}")

        # ── Step 6: Configure Nginx ──
        print("\n🌐 配置 Nginx 路由...")

        check_cmd = "grep -q '8792' /etc/nginx/conf.d/brain.conf 2>/dev/null && echo 'EXISTS' || echo 'MISSING'"
        nginx_exists = run_ssh(client, check_cmd)[0].strip()

        if "MISSING" in nginx_exists:
            nginx_snippet = f"""location /brain/ {{
    proxy_pass http://127.0.0.1:8792/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection 'upgrade';
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_cache_bypass $http_upgrade;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
}}

location /brain/static/ {{
    alias {NGINX_ROOT}/;
    expires 7d;
    add_header Cache-Control "public, immutable";
}}
"""
            write_nginx_cmd = f"""sudo mkdir -p /etc/nginx/conf.d
sudo tee /etc/nginx/conf.d/brain.conf > /dev/null << 'NGINXEOF'
{nginx_snippet}
NGINXEOF
echo 'NGINX_WRITTEN'
"""
            out, err = run_ssh(client, write_nginx_cmd)
            if "NGINX_WRITTEN" in out:
                print("  ✅ Nginx brain 配置已写入 /etc/nginx/conf.d/brain.conf")
            else:
                print(f"  ⚠️ Nginx 配置写入失败: {out[:100]}")
        else:
            print("  ✅ Nginx 路由已存在，跳过")

        # Test and reload Nginx
        out, err = run_ssh(client, "sudo nginx -t 2>&1 && sudo systemctl reload nginx 2>&1")
        print(f"  📋 Nginx: {out.strip()}")

        # ── Step 6.5: Copy frontend to Nginx static dir ──
        print("\n📋 同步前端到 Nginx 静态目录...")
        copy_frontend = f"""
        sudo mkdir -p {NGINX_ROOT}/frontend
        sudo cp {REMOTE_BASE}/frontend/index.html {NGINX_ROOT}/frontend/index.html
        sudo chown -R www-data:www-data {NGINX_ROOT}
        echo 'FRONTEND_SYNCED'
        """
        out, err = run_ssh(client, copy_frontend)
        if "FRONTEND_SYNCED" in out:
            print("  ✅ 前端已同步到 Nginx")

        # ── Step 7: Health check ──
        print("\n🏥 健康检查...")
        time.sleep(3)

        # Check internal port
        out, err = run_ssh(client, "curl -s http://127.0.0.1:8792/api/brain/health 2>&1")
        print(f"  📡 内部健康: {out[:200]}")

        # Check via Nginx
        out, err = run_ssh(client, "curl -s http://127.0.0.1/brain/api/brain/health 2>&1")
        print(f"  🌐 Nginx 代理: {out[:200]}")

        # Check systemd
        out, err = run_ssh(client, "sudo systemctl is-active startrack-brain 2>&1")
        print(f"  ⚡ systemd: {out.strip()}")

        sftp.close()
        client.close()

        print("\n" + "=" * 50)
        print("✅ 星轨智库部署完成!")
        print(f"   API: https://ai.gxxgcl.xyz/brain/api/brain/health")
        print(f"   前端: https://ai.gxxgcl.xyz/brain/static/frontend/index.html")
        print(f"   文档: https://ai.gxxgcl.xyz/brain/docs")
        print("=" * 50)

    except Exception as e:
        print(f"\n❌ 部署失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    deploy()
