"""Toolchain registry, executable smoke projects, and named application profiles."""
import re
from pathlib import Path

DETAILS = {
 'python-app':('Python packages and ASGI/WSGI apps','python','python',
   'game.pytest_project(), game.python_typecheck(), game.python_build(), game.python_request(module,factory,path). Use hash-locked requirements.lock or uv.lock; wheel-only offline installation. setuptools.build_meta, pytest tests, no embedded wheel bytes.'),
 'wasm':('WASI Preview 1 C/C++ and Rust','c','rust',
   'game.wasi_build(source), game.wasi_rust(source), game.wasi_run(path,args=None,preopens=None,fuel=10000000). Explicit preopens and bounded memory/fuel. WASI Preview 1, wasm32-wasip1, no browser/component model APIs.'),
 'kotlin-jvm':('Kotlin/JVM Gradle application','kotlin','java',
   'game.kotlin_build(), game.kotlin_test(), game.kotlin_run(args=None). Gradle 8.14.3, Kotlin 2.2.21, JDK 21, JUnit 5.11.4. Offline Gradle. Extra Maven jars require forge-dependencies.lock.json coordinates and SHA256; use implementation(fileTree("/deps/jars") { include("*.jar") }).'),
 'hdl':('Verilog/SystemVerilog simulation and synthesis','verilog','c-cpp',
   'game.hdl_sim(top,sources=None), game.hdl_lint(sources=None), game.hdl_synth(top,sources=None). Icarus, Verilator, Yosys. Self-checking cycle/reset/overflow tests, $fatal on failures, VCD outputs. Simulation/synthesis only; no bitstream deployment or physical timing claims.'),
 'cad':('Parametric mechanical CAD with OpenSCAD','scad','c-cpp',
   'game.cad_export(source="model.scad"), game.cad_mesh(path), game.cad_render(). Assert bounds, size, volume, watertight and faces. Units millimetres, preserve wall thickness and clearances. STL and PNG; no STEP/assembly support.'),
 'infra':('Offline Helm/Kustomize Kubernetes manifests','yaml','shell',
   'game.helm_render(), game.kustomize_render(), game.infra_validate(text), game.infra_policy(text). Kubernetes 1.32 schemas, unknown kinds fail. Assert ports, resources, nonroot, no privilege escalation, readonly roots and pinned images. No apply, cluster access or credentials.'),
 'android':('Android Kotlin APK and JVM tests','kotlin','java',
   'game.android_build(), game.android_test(), game.android_apks(). SDK API 35, build tools 35.0.0, AGP 8.9.2, Kotlin 2.2.21, JVM target 17. Offline Gradle; APKs and JVM tests only. SDK license requires explicit acceptance. Emulator instrumentation is a separate capability.'),
 'ros2':('ROS 2 Jazzy headless Python nodes','python','python',
   'game.ros_build(), game.ros_test(), game.ros_launch(path), game.ros_replay(module,recording,function="transform"). rclpy/std_msgs Float64, deterministic finite sensor JSON recordings, local DDS only, timeouts. No Gazebo or physical robot deployment. Lock external Python wheels; additional ROS system packages require a reviewed image.'),
 'desktop':('Native Qt/PySide6 applications','python','python',
   'game.desktop_test(), game.desktop_build(), game.desktop_run(args=None), game.desktop_capture(). PySide6 6.9.3 and PyInstaller 6.16.0. Real keyboard/accessibility assertions and offscreen screenshots. Linux packaging here. Windows/macOS require native workers; signing is a separate approved-artifact action.'),
}
TOOLCHAINS = {n:dict(lang=d[0],fence=d[1],test_help=d[3],coder_help=d[3],memory='3g',tmpfs_mb=1536,
                      timeout=900,pids=256) for n,d in DETAILS.items()}
TOOLCHAINS['android'].update(memory='6g',tmpfs_mb=4096,timeout=1800)

SELFTEST_FILES = {
 'python-app':{
  'pyproject.toml':'[build-system]\nrequires=["setuptools","wheel"]\nbuild-backend="setuptools.build_meta"\n[project]\nname="forge-answer"\nversion="1.0.0"\n[tool.setuptools]\npy-modules=["answer"]\n',
  'answer.py':'def answer() -> int:\n return 42\ndef app(environ,start_response):\n start_response("200 OK",[("Content-Type","text/plain")]);return [b"42"]\n',
  'tests/test_answer.py':'from answer import answer\ndef test_answer():assert answer()==42\n'},
 'wasm':{'hello.c':'#include <stdio.h>\nint main(){puts("42");return 0;}\n',
   'hello.rs':'fn main(){println!("42");}\n',
   'denied.c':'#include <stdio.h>\nint main(){FILE *f=fopen("/etc/passwd","r");if(f){fclose(f);return 1;}puts("denied");return 0;}\n',
   'trap.c':'int main(){__builtin_trap();}\n'},
 'kotlin-jvm':{
   'settings.gradle.kts':'rootProject.name="answer"\n',
   'build.gradle.kts':'plugins { kotlin("jvm") version "2.2.21"; application }\nrepositories { mavenCentral() }\ndependencies { testImplementation("org.junit.jupiter:junit-jupiter:5.11.4") }\ntasks.test { useJUnitPlatform() }\napplication { mainClass.set("MainKt") }\nkotlin { jvmToolchain(21) }\n',
   'src/main/kotlin/Main.kt':'fun answer(): Int = 42\nfun main(){println(answer())}\n',
   'src/test/kotlin/AnswerTest.kt':'import org.junit.jupiter.api.Test\nimport org.junit.jupiter.api.Assertions.assertEquals\nclass AnswerTest { @Test fun works(){assertEquals(42,answer())} }\n'},
 'hdl':{
   'counter.sv':'module counter(input clk,input reset,output reg [3:0] count);always @(posedge clk) if(reset) count<=0;else count<=count+1;endmodule\n',
   'tb.sv':'module tb; reg clk=0;reg reset=1;wire [3:0] count;counter dut(clk,reset,count);always #5 clk=~clk;initial begin $dumpfile("counter.vcd");$dumpvars(0,tb);#6;if(count!==0)$fatal(1,"reset");reset=0;#10;if(count!==1)$fatal(1,"cycle");#150;if(count!==0)$fatal(1,"overflow");$display("42");$finish;end endmodule\n'},
 'cad':{'model.scad':'difference(){cube([20,30,5]);translate([10,15,-1])cylinder(h=7,r=2,$fn=48);}\n'},
 'infra':{'kustomization.yaml':'resources: [deployment.yaml]\n',
  'deployment.yaml':'apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: answer}\nspec:\n  replicas: 1\n  selector: {matchLabels: {app: answer}}\n  template:\n    metadata: {labels: {app: answer}}\n    spec:\n      securityContext: {runAsNonRoot: true}\n      containers:\n      - name: answer\n        image: example/answer:1.0.0\n        ports: [{containerPort: 8080}]\n        resources: {requests: {cpu: 100m, memory: 64Mi}, limits: {cpu: 500m, memory: 128Mi}}\n        securityContext: {allowPrivilegeEscalation: false, readOnlyRootFilesystem: true}\n'},
 'android':{
   'settings.gradle.kts':'pluginManagement { repositories { google();mavenCentral();gradlePluginPortal() } }\ndependencyResolutionManagement { repositories { google();mavenCentral() } }\nrootProject.name="ForgeAnswer"\ninclude(":app")\n',
   'build.gradle.kts':'plugins { id("com.android.application") version "8.9.2" apply false; kotlin("android") version "2.2.21" apply false }\n',
   'app/build.gradle.kts':'plugins { id("com.android.application");kotlin("android") }\nandroid { namespace="org.forge.answer";compileSdk=35\n defaultConfig { applicationId="org.forge.answer"; minSdk=23; targetSdk=35;versionCode=1;versionName="1.0" }\n compileOptions { sourceCompatibility=JavaVersion.VERSION_17;targetCompatibility=JavaVersion.VERSION_17 }\n kotlinOptions { jvmTarget="17" } }\ndependencies { testImplementation("junit:junit:4.13.2") }\n',
   'app/src/main/AndroidManifest.xml':'<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application android:theme="@android:style/Theme.Material.Light"><activity android:name=".MainActivity" android:exported="true"><intent-filter><action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/></intent-filter></activity></application></manifest>',
   'app/src/main/java/org/forge/answer/MainActivity.kt':'package org.forge.answer\nimport android.app.Activity\nimport android.os.Bundle\nimport android.widget.TextView\nfun answer()=42\nclass MainActivity:Activity(){override fun onCreate(state:Bundle?){super.onCreate(state);setContentView(TextView(this).apply{text=answer().toString()})}}\n',
   'app/src/test/java/org/forge/answer/AnswerTest.kt':'package org.forge.answer\nimport org.junit.Test\nimport org.junit.Assert.assertEquals\nclass AnswerTest { @Test fun works(){assertEquals(42,answer())} }\n'},
 'ros2':{'sensor.py':'def transform(value):return value*2\n', 'recording.json':'[1,2,3]',
   'setup.py':'from setuptools import setup\nsetup(name="forge_sensor",version="1.0.0",py_modules=["sensor"],data_files=[("share/ament_index/resource_index/packages",["resource/forge_sensor"]),("share/forge_sensor",["package.xml"])])\n',
   'setup.cfg':'[develop]\nscript_dir=$base/lib/forge_sensor\n[install]\ninstall_scripts=$base/lib/forge_sensor\n',
   'resource/forge_sensor':'',
   'package.xml':'<package format="3"><name>forge_sensor</name><version>1.0.0</version><description>Recorded sensor fixture</description><maintainer email="forge@example.com">Forge</maintainer><license>MIT</license><buildtool_depend>ament_python</buildtool_depend><exec_depend>rclpy</exec_depend><exec_depend>std_msgs</exec_depend><export><build_type>ament_python</build_type></export></package>',
   'tests/test_sensor.py':'from sensor import transform\ndef test_transform():assert transform(21)==42\n',
   'launch/sensor.launch.py':"from launch import LaunchDescription\nfrom launch.actions import ExecuteProcess\ndef generate_launch_description():return LaunchDescription([ExecuteProcess(cmd=['python3','-c',\"print('LAUNCH_OK')\"],output='screen')])\n"},
 'desktop':{'app.py':'''import sys
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QLabel,QPushButton
from PySide6.QtCore import QTimer
def make_window():
 w=QWidget();w.setWindowTitle('Forge Counter');layout=QVBoxLayout(w);label=QLabel('41');label.setAccessibleName('Count');button=QPushButton('Increment');button.setAccessibleName('Increment');button.clicked.connect(lambda:label.setText(str(int(label.text())+1)));layout.addWidget(label);layout.addWidget(button);return w
if __name__=='__main__':
 app=QApplication(sys.argv);window=make_window();window.show()
 if '--selftest' in sys.argv:QTimer.singleShot(100,app.quit)
 sys.exit(app.exec())
''',
 'tests/test_app.py':'''from PySide6.QtWidgets import QApplication,QPushButton,QLabel
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from app import make_window
def test_keyboard():
 app=QApplication.instance() or QApplication([]);w=make_window();w.show();b=w.findChild(QPushButton);b.setFocus();QTest.keyClick(b,Qt.Key_Space);app.processEvents();assert w.findChild(QLabel).text()=='42';assert b.accessibleName()=='Increment';w.close()
'''}
}
SELFTEST_STEPS = {
 'python-app':'''r=game.pytest_project();r.assert_ok()
assert game.python_typecheck(['answer.py']).ok
assert game.python_request('answer')['body']=='42'
assert game.python_build().ok
(game.project_dir()/'tests/test_bad.py').write_text('def test_bad():assert False\\n')
assert not game.pytest_project().ok
import shutil
shutil.rmtree(game.project_dir()/'.forge-venv')
(game.project_dir()/'requirements.lock').write_text('forge-missing-package==1.0 --hash=sha256:'+'a'*64)
try:game.pytest_project()
except AssertionError as e:assert 'offline wheels' in str(e)
else:raise AssertionError('missing wheel accepted')
''',
 'wasm':'''assert game.wasi_build('hello.c').ok
assert game.wasi_run('app.wasm').out.strip()=='42'
assert game.wasi_rust('hello.rs','rust.wasm').ok
assert game.wasi_run('rust.wasm').out.strip()=='42'
assert game.wasi_build('denied.c','denied.wasm').ok
assert game.wasi_run('denied.wasm').out.strip()=='denied'
assert game.wasi_build('trap.c','trap.wasm').ok
assert not game.wasi_run('trap.wasm').ok
''',
 'kotlin-jvm':'''assert game.kotlin_build().ok
game.kotlin_test().assert_ok()
assert '42' in game.kotlin_run().out
(game.project_dir()/'src/main/kotlin/Bad.kt').write_text('invalid Kotlin')
assert not game.kotlin_build().ok
''',
 'hdl':'''r=game.hdl_sim('tb');assert r.ok and '42' in r.out,r.out
assert game.hdl_lint().ok
r=game.hdl_synth('counter',['counter.sv']);assert r.ok,r.out
(game.project_dir()/'bad.sv').write_text('invalid Verilog')
assert not game.hdl_sim('tb').ok
''',
 'cad':'''r=game.cad_export();assert r.ok,r.out
m=game.cad_mesh('model.stl');assert m['size']==[20,30,5] and m['watertight'] and 2900<m['volume']<3000,m
assert game.cad_render().ok
(game.project_dir()/'model.scad').write_text('invalid SCAD ???')
assert not game.cad_export().ok
''',
 'infra':'''r=game.kustomize_render();assert r.ok,r.out
v=game.infra_validate(r.out);assert v.ok,v.out
game.infra_policy(r.out).assert_ok()
assert not game.infra_validate(r.out.replace('replicas: 1','replicas: wrong')).ok
assert not game.infra_policy(r.out.replace('readOnlyRootFilesystem: true','readOnlyRootFilesystem: false')).ok
''',
 'android':'''r=game.android_build();assert r.ok,r.out
game.android_test().assert_ok();assert game.android_apks()
(game.project_dir()/'app/src/main/java/org/forge/answer/Bad.kt').write_text('invalid Kotlin')
assert not game.android_build().ok
''',
 'ros2':'''r=game.ros_build();assert r.ok,r.out
game.ros_test().assert_ok()
assert game.ros_replay('sensor','recording.json')==[2,4,6]
r=game.ros_launch('launch/sensor.launch.py');assert r.ok and 'LAUNCH_OK' in r.out,r.out
(game.project_dir()/'tests/test_bad.py').write_text('def test_bad():assert False\\n')
assert not game.ros_test().ok
''',
 'desktop':'''game.desktop_test().assert_ok()
assert game.desktop_capture().is_file()
r=game.desktop_build();assert r.ok,r.out
r=game.desktop_run(['--selftest']);assert r.ok,r.out
(game.project_dir()/'tests/test_bad.py').write_text('def test_bad():assert False\\n')
assert not game.desktop_test().ok
'''}

PROFILES = {'node:frontend':dict(image='forge-sandbox-ux:latest',help='Vite/React/Vue frontend in the ux image. game.frontend_build(), game.browser_check(actions=[role/name/key/text dicts]), game.frontend_package(out). Real keyboard, axe and desktop/mobile captures; no network, native npm addons need reviewed images.'),
 **{tc+':database-service':dict(image='forge-sandbox-'+tc+'-database:latest',help='PostgreSQL 17 in the same offline sandbox. game.pg_apply(), game.pg().query/psql, game.database_env(), game.service_request(command,path,env=...). Assert rollback, HTTP errors and restart persistence. No Compose or deployment.') for tc in ('python-app','node','go')}}


def spec_profile(spec, target):
    match=re.search(r'^\s*Profile\s*:\s*([\w-]+)\s*$',re.sub(r'<!--.*?-->','',spec,flags=re.S),re.M|re.I)
    return validate(target,match.group(1).lower() if match else '')


def validate(target,profile):
    if profile and target+':'+profile not in PROFILES:raise ValueError('unsupported profile '+profile+' for '+target)
    return profile


def profile_smoke(target,profile):
    validate(target,profile)
    if profile=='frontend':
        return {'package.json':'{"name":"counter","private":true,"type":"module"}',
            'index.html':'<!doctype html><html lang="en"><head><meta charset="UTF-8"><title>Counter</title></head><body><main><h1>Counter</h1><div id="root"></div></main><script type="module" src="/main.jsx"></script></body></html>',
            'main.jsx':"import React from 'react';import{createRoot}from'react-dom/client';function App(){const[n,setN]=React.useState(41);return <><p aria-live='polite'>{n}</p><button onClick={()=>setN(n+1)}>Increment</button></>};createRoot(document.getElementById('root')).render(<App/>);"},'''
r=game.frontend_build();assert r.ok,r.out
report=game.browser_check(actions=[dict(role='button',name='Increment',key='Space',text='42')])
assert not report['violations'] and report['keyboard']['focusable']>=1 and not report['mobile_overflow'],report
assert game.frontend_package(game.TMP/'static')
(game.project_dir()/'main.jsx').write_text('export const broken = <')
assert not game.frontend_build().ok
'''
    files={'migrations/001_schema.sql':'CREATE TABLE scores(id integer PRIMARY KEY,value integer NOT NULL CHECK(value>=0));INSERT INTO scores VALUES(1,42);'}
    if target=='python-app':
        files['service.py']='''import os
import psycopg
from http.server import HTTPServer,BaseHTTPRequestHandler
class H(BaseHTTPRequestHandler):
 def do_GET(self):
  if self.path!='/score':self.send_error(404);return
  with psycopg.connect('') as c:value=c.execute('SELECT value FROM scores WHERE id=1').fetchone()[0]
  self.send_response(200);self.end_headers();self.wfile.write(str(value).encode())
HTTPServer(('127.0.0.1',int(os.environ['PORT'])),H).serve_forever()
''';command="[sys.executable,'service.py']";prelude='import sys\n'
    elif target=='node':
        files['service.cjs']="const{Client}=require('pg');require('node:http').createServer(async(q,r)=>{if(q.url!='/score'){r.writeHead(404);r.end();return}const c=new Client();try{await c.connect();const result=await c.query('SELECT value FROM scores WHERE id=1');r.end(String(result.rows[0].value))}catch(e){r.writeHead(500);r.end()}finally{await c.end()}}).listen(Number(process.env.PORT),'127.0.0.1');"
        command="['node','service.cjs']";prelude='game.node_modules()\n'
    else:
        files['go.mod']='module forge-service\n\ngo 1.24.0\n'
        files['main.go']='''package main
import("net/http";"os";"os/exec";"strings")
func main(){http.HandleFunc("/",func(w http.ResponseWriter,r *http.Request){if r.URL.Path!="/score"{http.NotFound(w,r);return};data,err:=exec.Command("psql","-XAt","-c","SELECT value FROM scores WHERE id=1").Output();if err!=nil{http.Error(w,"database error",500);return};w.Write([]byte(strings.TrimSpace(string(data))))});http.ListenAndServe("127.0.0.1:"+os.Getenv("PORT"),nil)}
''';command="['./service']";prelude="r=game.go('build','-o','service','.');assert r.ok,r.out\n"
    return files,prelude+'''
r=game.pg_apply();assert r.ok,r.out
srv=game.pg();env=game.database_env()
assert srv.query('SELECT value FROM scores',db='app')==[(42,)]
r=srv.psql('BEGIN;UPDATE scores SET value=99;INSERT INTO scores VALUES(1,1);COMMIT;',db='app')
assert not r.ok and srv.query('SELECT value FROM scores',db='app')==[(42,)]
assert game.service_request(COMMAND,'/score',env=env)['body']=='42'
assert game.service_request(COMMAND,'/missing',env=env)['status']==404
srv.stop();srv.start();assert srv.query('SELECT value FROM scores',db='app')==[(42,)]
(game.project_dir()/'migrations/002_invalid.sql').write_text('CREATE TABL invalid;')
assert not game.pg_apply(fresh=False).ok
'''.replace('COMMAND',command)


def install(g):
    g['TOOLCHAINS'].update(TOOLCHAINS);g['SELFTEST_FILES'].update(SELFTEST_FILES);g['SELFTEST_STEPS'].update(SELFTEST_STEPS)
    g['TOOLCHAIN_LANG'].update({n:d[2] for n,d in DETAILS.items()});g['PACKAGED']=(*g['PACKAGED'],*TOOLCHAINS)
    g.update(TOOLCHAIN_PROFILE='',PROFILES=PROFILES,profile_smoke=profile_smoke)
    original_set=g['set_toolchain']
    def set_toolchain(name,profile=''):
        original_set(name)
        try:g['TOOLCHAIN_PROFILE']=validate(name,profile)
        except ValueError as error:raise g['ForgeError'](str(error)) from error
    g['set_toolchain']=set_toolchain
    original_plan=g['Run'].plan.fget
    def plan(run):
        result=original_plan(run);tc=g['TOOLCHAIN']
        set_toolchain(tc,result.get('profile') or spec_profile(run.spec,tc))
        return result
    g['Run'].plan=property(plan)
    original_profile=g['Sandbox'].profile
    def profile(sandbox):
        result=original_profile(sandbox);selected=g['TOOLCHAIN_PROFILE']
        if selected:
            key=g['TOOLCHAIN']+':'+selected
            result.update(image=PROFILES[key]['image'],memory='3g',tmpfs_mb=1536,timeout=600,pids=256)
            result.update(getattr(sandbox,'named_profiles',{}).get(key,{}))
        return result
    original_init=g['Sandbox'].__init__
    def init(sandbox,cfg):
        original_init(sandbox,cfg);sandbox.named_profiles=cfg.get('profiles',{}) or {}
    g['Sandbox'].profile=profile;g['Sandbox'].__init__=init
    original_help=g['toolchain_help']
    def help(name=None):
        result=original_help(name)
        if g['TOOLCHAIN_PROFILE']:result+='\n'+PROFILES[g['TOOLCHAIN']+':'+g['TOOLCHAIN_PROFILE']]['help']
        return result
    g['toolchain_help']=help
