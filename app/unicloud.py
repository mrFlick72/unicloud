import sqlite3
import time
import atexit
from flask import Flask, g, render_template, jsonify, request
from flask_restful import Api, Resource, reqparse
from flask_basicauth import BasicAuth
from flask_autoindex import AutoIndex
from sqlite3 import Error
from client_mgt import ClientMgt
from share_mgt import ShareMgt
from homestats import *
from apscheduler.schedulers.background import BackgroundScheduler
from scheduler_tasks import *
from conf import *

root_dir     = "/data"
database     = root_dir + "/unicloud.db"
authkeyfile  = root_dir + "/.ssh/unicloud_authorized_keys"
#shares_path  = "/shares"

startTime = time.time()

app = Flask(__name__, static_url_path='/static')
api = Api(app)

if server_debug:
    print ("Debug is active..")
    app.debug = True
    from werkzeug.debug import DebuggedApplication
    app.wsgi_app = DebuggedApplication(app.wsgi_app, True)
else:
    print("Debug is disabled..")

app.config['BASIC_AUTH_USERNAME'] = server_ui_username
app.config['BASIC_AUTH_PASSWORD'] = server_ui_password
basic_auth = BasicAuth(app)


sql_table_shares = """ CREATE TABLE IF NOT EXISTS shares (
                         id INTEGER PRIMARY KEY,
                         size TEXT NOT NULL,
                         name TEXT NOT NULL,
                         description TEXT NOT NULL,
                         path TEXT NOT NULL); """

sql_table_events = """ CREATE TABLE IF NOT EXISTS events (
                         id INTEGER PRIMARY KEY,
                         client TEXT NOT NULL,
                         share TEXT NOT NULL,
                         log TEXT,
                         start_ts   DATETIME,
                         end_ts   DATETIME,
                         duration INTEGER,
                         sync_status TEXT,
                         status TEXT); """

sql_table_clients = """ CREATE TABLE IF NOT EXISTS clients (
                         id INTEGER PRIMARY KEY,
                         name TEXT NOT NULL,
                         ssh_key TEXT NOT NULL,
                         status TEXT NOT NULL,
                         share TEXT NOT NULL,
                         threshold INTEGER NOT NULL,
                         sync_status TEXT,
                         joindate DATETIME); """


def create_connection(db_file):
    """ create a database connection to a SQLite database """
    try:
        conn = sqlite3.connect(db_file)
        return conn
    except Error as e:
        print(e)

def create_table(conn, create_table_sql):
    """ create a table from the create_table_sql statement
    :param conn: Connection object
    :param create_table_sql: a CREATE TABLE statement
    :return:
    """
    try:
        c = conn.cursor()
        c.execute(create_table_sql)
    except Error as e:
        print(e)
#    conn.close()

# SCHEDULER

scheduler = BackgroundScheduler()
scheduler.add_job(func=scheduler_tasks_update_sync_status, trigger="interval", seconds=60, args=(app,))
scheduler.add_job(func=scheduler_tasks_share_update_size, trigger="interval", hours=6, args=(app,))
scheduler.add_job(func=scheduler_tasks_purge_logs, trigger="interval", hours=12, args=(app,))
scheduler.start()

# Shut down the scheduler when exiting the app
atexit.register(lambda: scheduler.shutdown())

### INIT DB AND TABLES

conn = create_connection(database)

if conn is not None:
     create_table(conn, sql_table_shares)
     create_table(conn, sql_table_events)
     create_table(conn, sql_table_clients)
     conn.close()
else:
    print ("Error! can't create database connection")
    print (conn)


### FLASK DB ###

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(database)
        #db.row_factory = sqlite3.Row
    return db

def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv

### FILTERS

# helper to close
@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
      db.close()

@app.template_filter('dt')
def _jinja2_filter_datetime(date, fmt=None):
    #date = int(time.time())
    if fmt:
        return time.strftime(fmt, time.localtime(date))
    if date is not None:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(date))
    else:
        return "None"

@app.template_filter('inc')
def _jinja2_filter_inc(number):
   number += 1
   return number

@app.template_filter('dec')
def _jinja2_filter_dec(number):
    number -= 1
    return number

@app.template_filter('sync_status')
def _jinja2_filter_sync_status(client):
    #date = int(time.time())
    cl=ClientMgt(client)
    sync_status = cl.sync_status()
    #print ("Checking status for %s, status %s" % (client, sync_status)  )
    return sync_status

# HOME #


@app.route("/", methods=['GET'])
@basic_auth.required
def home():
    sys_stats = homestats_sys(startTime)
    unicloud_stats = homestats_unicloud()
    runtime_stats = homestats_runtime()
    return render_template("index.html", sys_stats=sys_stats, unicloud_stats=unicloud_stats, runtime_stats=runtime_stats)

# DOC #


@app.route("/doc", methods=['GET'])
@basic_auth.required
def doc():
    return render_template("doc.html")

# ABOUT #


@app.route("/about", methods=['GET'])
@basic_auth.required
def about():
    return render_template("about.html")


# FILES
# ok
files_index = AutoIndex(app, shares_path, add_url_rules=False)
# Custom indexing
@app.route('/files/<path:path>', strict_slashes=False)
@app.route("/files", strict_slashes=False, methods=['GET'])
@basic_auth.required
def autoindex(path='.'):
   return files_index.render_autoindex(path)

#### CLIENTS REQUESTS #########

@app.route("/status", methods=['GET'])
def status():
   return "[OK] Ready to serve sir..\n" , 200

@app.route("/clients", methods=['GET'])
@basic_auth.required
def clients():
    query = """ SELECT clients.name,
                  clients.status,
                  clients.joindate,
                  clients.threshold,
                  clients.ssh_key,
                  max(events.end_ts)
                FROM clients
                LEFT JOIN events on events.client = clients.name
                GROUP BY clients.name
                ORDER BY events.end_ts desc """
    res = query_db(query)
    #print (res)
    return render_template("clients.html", clients=res)

@app.route("/clients/mgt", methods=['GET'])
@basic_auth.required
def client_mgt():
    client = ClientMgt("all")
    clientlist = client.list_clients()
    share = ShareMgt("all")
    sharelist = share.share_list()
    return render_template("client_mgt.html", clientlist=clientlist, sharelist=sharelist)

@app.route("/clients/status/<client>", methods=['GET'])
def client_status(client):
    cl=ClientMgt(client)
    exist = cl.exist()
    if exist[0] == 0:
      return "Client %s does not exist, register first\n" % client, 404
    else:
      status=cl.status()
      #result="\n".join(status[0])
      #print (status)
      if status[0][1] == "OK":
        return "Client %s status: [ %s ]\n" % (status[0][0], status[0][1]), 200
      else:
        return "Client %s need to be activated. Activate from server UI!" % status[0][0], 401
      #return jsonify(status)

@app.route("/clients/info/<client>", methods=['GET'])
def client_info(client):
    cl = ClientMgt(client)
    exist = cl.exist()
    if exist[0] == 0:
      return "Client %s does not exist, register first\n" % client, 404
    else:
      status=cl.info()
      return jsonify(status)

@app.route("/clients/info/ui/<client>", methods=['GET'])
@basic_auth.required
def client_info_ui(client):
    cl=ClientMgt(client)
    exist = cl.exist()
    status = cl.info()
    #print (status)
    if status['threshold'] > 0:
      sync_status = cl.sync_status()
    else:
      sync_status = 0
    if exist[0] == 0:
      return "Client %s does not exist, register first\n" % client, 404
    else:
      status=cl.info()
      return render_template("client_info.html", status=status, client=client, sync_status=sync_status)

@app.route("/clients/register", methods=['POST'])
def client_register():
    name = request.form.get('name')
    ssh_key = request.form.get('ssh_key')
    share = request.form.get('share')
    register_type = "join"
    if name is not None and ssh_key is not None:
       client = ClientMgt(name)
       exist = client.exist()
       #print (exist)
       if exist[0] > 0:
           result = "Error Client %s already exist" % name
           rc = 500
       else:
           print(ssh_key)
           print(authkeyfile)
           client.add(ssh_key, authkeyfile, register_type, share)
           result = "Client %s added successfully, Activate it from server UI!" % name
           rc = 200
    else:
        result = "Incomplete request"
        rc = 500
    return jsonify(result), rc

@app.route("/clients/add/process", methods=['POST'])
@basic_auth.required
def client_process():
    name = request.form.get('name')
    ssh_key = request.form.get('ssh_key')
    share = request.form.get('share')
    register_type = "ui"
    if name is not None or ssh_key is not None:
       client = ClientMgt(name)
       if client.exist()[0] > 0:
           result = "Error Client %s already exist" % name
           rc = 500
       else:
           result = "\n".join(client.add(ssh_key, authkeyfile, register_type, share))
           rc = 200
       return render_template("client_mgt_result.html", result=result), rc

@app.route("/clients/del/process", methods=['POST'])
@basic_auth.required
def del_process():
    name = request.form.get('del_name')
    if name is not None:
       client = ClientMgt(name)
       print (client.exist()[0])
       if client.exist()[0] == 0:
           result = "Client %s does not exist" % name
       else:
           client.remove(authkeyfile)
           result = "Client %s removed successfully" % name
       return render_template("client_mgt_result.html", result=result), 200


@app.route("/clients/activate/process", methods=['POST'])
@basic_auth.required
def activate_process():
    name = request.form.get('name')
    ssh_key = request.form.get('ssh_key')
    if name is not None:
       client = ClientMgt(name)
       result = "\n".join(client.activate(ssh_key,authkeyfile))
       print (result)
       return render_template("client_activate_result.html", result=result), 200

@app.route("/clients/threshold/process", methods=['POST'])
@basic_auth.required
def set_threshold():
    name = request.form.get('name')
    threshold = request.form.get('threshold')
    client = ClientMgt(name)
    result = client.set_threshold(int(threshold))
    print (result)
    return render_template("client_threshold_result.html", result=result, name=name), 200

#### SHARES REQUESTS ######

@app.route("/shares", methods=['GET'])
@basic_auth.required
def shares():
    query = "select name, description, size, path from shares"
    res = query_db(query)
    return render_template("shares.html", shares=res)


@app.route("/shares/info/ui/<name>", methods=['GET'])
@basic_auth.required
def shares_info_ui(name):
    info = "all"
    share = ShareMgt(name)
    result = share.info(info)
    if not result:
      result = "Error, %s does not exist\n" % name
      return result, 404
    else:
      return render_template("share_info.html", share=result)

@app.route("/shares/info/<name>")
def share_info(name):
    info = "all"
    share = ShareMgt(name)
    result = share.info(info)
    if not result:
      result = "Error, %s does not exist\n" % name
      return result, 404
    else:
      return jsonify(result), 200

@app.route("/shares/info/<name>/path")
def share_info_path(name):
    info = "path"
    share = ShareMgt(name)
    result = share.info(info)
    if not result:
      result = "Error, %s does not exist\n" % name
      return result, 404
    else:
      return result + "\n", 200

@app.route("/shares/mgt", methods=['GET'])
@basic_auth.required
def share_mgt():
    share = ShareMgt("all")
    sharelist = share.share_list()
    return render_template("share_mgt.html", shares_path=shares_path, sharelist=sharelist)

@app.route("/shares/add/process", methods=['POST'])
@basic_auth.required
def share_add_process():
    name = request.form.get('name')
    path = request.form.get('path')
    description = request.form.get('description')
    ssh_key = request.form.get('ssh_key')
    create = request.form.get('create')
    if name is not None or path is not None or description is not None:
       share = ShareMgt(name)
       result = share.add(path, description, create)
       #print (result)
       if result is not True:
           result = "Error, share %s or path %s already exist" % (name, path)
           rc = 500
       else:
           result = "Share %s added successfully<br>Path: %s" % ( name, path)
           rc = 200
    else:
       result = "Please Fill all the fields in the form..."
    return render_template("share_mgt_result.html", result=result), rc

@app.route("/shares/del/process", methods=['POST'])
@basic_auth.required
def share_del_process():
    name = request.form.get('name')
    path = request.form.get('path')
    delete = request.form.get('delete')
    if name is not None or path is not None:
       share = ShareMgt(name)
       result = share.delete(path, description)
       print (result)
       if result is not True:
           result = "Error, Path %s does not exist or share not present" % path
           rc = 500
       else:
           result = "Share %s Removed successfully\n Path: %s" % (name, path)
           rc = 200
    else:
       result = "Please Fill all the fields in the form..."
    return render_template("share_mgt_result.html", result=result), rc


@app.route("/shares/getsize/<name>/process", methods=['POST'])
@basic_auth.required
def share_get_size_process(name):
    share = ShareMgt(name)
    share.getsize()
    result = "Refreshing Share %s size" % name
    return render_template("share_mgt_result.html", result=result), 200


@app.route("/shares/exist", methods=['POST'])
def shares_exist():
    path = request.form.get('path')
    if path is not None:
       share = ShareMgt('', path, '')
       exist = share.exist()
       #print (exist)
       if exist[0] == 0:
           result = "Error, share %s does not exist" % path
           rc = 500
       else:
           result = "Ok share %s exist!" % path
           rc = 200
    else:
        result = "Incomplete request"
        rc = 500
    return jsonify(result), rc

#### EVENTS HTML ##########

@app.route("/events", methods=['PUT','POST','GET'])
@basic_auth.required
def events():
    client = request.form.get('client')
    status = request.form.get('status')
    sync_status = request.form.get('sync_status')
    limit = request.form.get('limit')
    #print ("Client %s, Status %s" % (client, status) )
    if limit is None:
      limit = 50
    #print ("Client %s Status %s" % (client,status))

    # ALL RESULTS
    if client == "ALL" and  status == "ALL" and sync_status== "ALL" or client is None and status is None and sync_status is None: # ALL RESULTS
      query = "select client, start_ts, end_ts, status, duration, share, id, sync_status from events order by start_ts desc limit %d" % int(limit)
    # SPECIFIC CLIENT AND ALL STATUS AND ALL SYNC_STATUS
    elif client != "ALL" and status == "ALL" and sync_status == "ALL":
      query = "select client, start_ts, end_ts, status, duration, share, id, sync_status from events where client = '%s' order by start_ts desc limit %d" % (client,int(limit))
    # SPECIFIC CLIENT AND SPECIFIC STATUS AND ALL SYNC_STATUS
    elif client != "ALL" and status != "ALL" and sync_status == "ALL":
        query = "select client, start_ts, end_ts, status, duration, share, id, sync_status from events where client = '%s' and status = '%s' order by start_ts desc limit %d" % (client, status, int(limit))
    # SPECIFIC CLIENT AND SPECIFIC SYNC_STATUS AND ALL STATUS
    elif client != "ALL" and sync_status != "ALL" and status == "ALL":
        query = "select client, start_ts, end_ts, status, duration, share, id, sync_status from events where client = '%s' and sync_status = '%s' order by start_ts desc limit %d" % (client, sync_status, int(limit))
    # SPECIFIC CLIENT AND SPECIFIC STATUS AND SPECIFIC SYNC_STATUS
    elif client != "ALL" and status != "ALL" and sync_status != "ALL":
        query = "select client, start_ts, end_ts, status, duration, share, id, sync_status from events where client = '%s' and status = '%s' and sync_status = '%s' order by start_ts desc limit %d" % (client, status, sync_status, int(limit))
    # SPECIFIC STATUS AND ALL CLIENTS
    elif status != "ALL" and client == "ALL" and sync_status == "ALL":
      query = "select client, start_ts, end_ts, status, duration, share, id, sync_status from events where status = '%s' order by start_ts desc limit %d" % (status, int(limit))
    # SPECIFIC SYNC_STATUS AND ALL CLIENTS
    elif sync_status != "ALL" and client == "ALL" and status == "ALL":
        query = "select client, start_ts, end_ts, status, duration, share, id, sync_status from events where sync_status = '%s' order by start_ts desc limit %d" % (sync_status, int(limit))
    # SPECIFIC SYNC_STATUS AND SPECIFIC STATUS
    elif sync_status != "ALL" and client == "ALL" and status != "ALL":
        query = "select client, start_ts, end_ts, status, duration, share, id, sync_status from events where sync_status = '%s' and status ='%s' order by start_ts desc limit %d" % (sync_status, status, int(limit))
    #print ("Query %s" % query)
    res = query_db(query)
    client = ClientMgt("all")
    clientlist = client.list_clients()
    return render_template("events.html", events=res, clientlist=clientlist), 200


@app.route("/events/<id>", methods=['GET'])
@basic_auth.required
def event_id(id):
    query = "select count(id) from events where id=%d and status is not 'SYNCING'" % int(id)
    if int(query_db(query)[0][0]) > 0:
       query = "select id,client,status,log,start_ts,end_ts,duration,share from events where id=%d" % int(id)
       res = query_db(query)
       return render_template("event_log.html", event=res)
    else:
       return render_template("event_404.html", id=int(id)), 404

####  SYNC ENDPOINTS ####

@app.route("/sync/start/<client>", methods=['PUT','POST'])
def sync_start(client):
    share = request.form.get('share')
    start_ts = int(request.form.get('start_ts'))
    clientmgt = ClientMgt(client)
    if clientmgt.exist()[0] == 0:
      return "Client %s does not exist, register first" % client, 500
    else:
      clientmgt.check_pending()
      status = "SYNCING"
      query = ("insert into events (client,start_ts,share,status) values ('%s',%d,'%s','%s')") % (client, start_ts, share, status)
      #print (query)
      query_db(query)
      get_db().commit()
      return "Sync Started, record updated with status %s" % status, 200

@app.route("/sync/end/<client>", methods=['PUT','POST'])
def sync_end(client):
    share = request.form.get('share')
    start_ts = int(request.form.get('start_ts'))
    status = request.form.get('status')
    sync_status = request.form.get('sync_status')
    log = request.form.get('log')
    end_ts = int(request.form.get('end_ts'))
    duration = end_ts - start_ts
    clientmgt = ClientMgt(client)
    #print("%s : Log Sync Enc Received: %s" % (client, log) )
    if clientmgt.exist()[0] == 0:
      return "Client %s does not exist, register first" % client, 500
    else:
      query = ("update events set status='%s', sync_status='%s', end_ts=%d, duration=%d, log='%s' where client='%s' and start_ts=%d") % (status, sync_status, end_ts, duration, log, client, start_ts)
      query_db(query)
      get_db().commit()
      return "Sync Terminated, record updated with status %s, duration %d" % (status, duration) , 201

############

#app.run(debug=True,port=8080)
