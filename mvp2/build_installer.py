
import os
import subprocess
import shutil
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

def build_installer():
    logger.info("Starting SpineEdge Installer Build...")
    
    # 0. Kill existing instances
    try:
        subprocess.run(["taskkill", "/F", "/IM", "SpineEdge.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import time
        time.sleep(1) # Wait for release
    except Exception: pass

    # 1. Clean previous build
    def remove_readonly(func, path, excinfo):
        import stat
        os.chmod(path, stat.S_IWRITE)
        func(path)

    if os.path.exists('dist'):
        try:
            shutil.rmtree('dist', onerror=remove_readonly)
        except Exception as e:
            logger.warning(f"Could not fully clean 'dist' folder: {e}")
            
    if os.path.exists('build'):
        try:
            shutil.rmtree('build', onerror=remove_readonly)
        except Exception as e:
            logger.warning(f"Could not fully clean 'build' folder: {e}")
        
    # 2. Run PyInstaller
    # Use python -m PyInstaller to avoid PATH issues
    import sys
    cmd = [sys.executable, '-m', 'PyInstaller', 'SpineEdge.spec', '--clean', '--noconfirm']
    
    try:
        # Redirect stderr to stdout to prevent pipe buffer deadlock
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        
        # Stream output
        while True:
            output = process.stdout.readline()
            if output == '' and process.poll() is not None:
                break
            if output:
                print(output.strip())
                
        rc = process.poll()
        
        if rc == 0:
            logger.info("Build Successful!")
            src = 'dist/SpineEdge.exe'
            dst = 'dist/Spinedge-Installer.exe'
            if os.path.exists(src):
                os.replace(src, dst)
                size_mb = os.path.getsize(dst) / (1024*1024)
                logger.info(f"Executable created at {dst} ({size_mb:.2f} MB)")
            else:
                logger.error("Build reported success but executable not found.")
        else:
            logger.error(f"Build failed with return code {rc}")
            print(process.stderr.read())
            
    except Exception as e:
        logger.error(f"An error occurred: {e}")

if __name__ == "__main__":
    build_installer()
