import time
from db_conn import get_db, query_db

class ClientMgt(object):
    client = ""
    def __init__(self, client):
        self.client = client

    def lastseen(self):
        query = "select end_ts from events where client='%s' order by end_ts limit 1;" % self.client
        result = query_db(query)
        return result

    def info(self):
        result = {}
        query = "select count(status) from events where client='%s' and status='OK';" %self.client
        count_ok=query_db(query)[0]
        query = "select count(status) from events where client='%s' and status='KO';" %self.client
        count_ko = query_db(query)[0]
        #count_total = count_ok + count_ko
        query = "select end_ts from events where client='%s' order by id desc limit 1;" %self.client
        lastseen = query_db(query)
        query = "select joindate from clients where name='%s';" % self.client
        joindate = query_db(query)
        query = "select status from clients where name='%s';" % self.client
        status = query_db(query)
        query = "select ssh_key from clients where name='%s';" % self.client
        ssh_key = query_db(query)
        query = "select share from clients where name='%s';" % self.client
        share = query_db(query)
        query = "select threshold from clients where name='%s';" % self.client
        threshold = query_db(query)
        #print ("last seen: %s" % lastseen)
        query = "select avg(duration) from events where client='%s';" % self.client
        avg_duration = query_db(query)
        if lastseen == []:
            result.update({'share': share[0][0], 'ok': count_ok[0], 'ko': count_ko[0], 'total': count_ok[0]+count_ko[0],
                           'lastseen': 'Never', 'joindate': joindate[0][0], 'status': status[0][0], 'ssh_key': ssh_key[0][0],
                           'threshold': threshold[0][0], 'avg_duration': "None" })
        else:
            result.update({'share': share[0][0], 'ok': count_ok[0], 'ko': count_ko[0], 'total': count_ok[0]+count_ko[0],
                           'lastseen': lastseen[0][0], 'joindate': joindate[0][0], 'status': status[0][0], 'ssh_key': ssh_key[0][0],
                           'threshold': threshold[0][0], 'avg_duration': float("{0:.2f}".format(avg_duration[0][0]))})
        return result


    def status(self):
        query = "select name,status from clients where name='%s'" % self.client
        result = query_db(query)
        return result

    def exist(self):
        query = "select count(name) from clients where name='%s'" % self.client
        result = query_db(query)
        return result[0]

    def add(self, ssh_key, authkeyfile, register_type, share):
        result = []
        self.ssh_key = ssh_key
        self.authkeyfile = authkeyfile
        self.register_type = register_type
        self.share = share
        if register_type == "ui":
          result.append(self.add_to_db(self.register_type, self.ssh_key, self.share))
          result.append(self.add_to_keyfile(self.authkeyfile, self.ssh_key))
        elif register_type == "join":
          result.append(self.add_to_db(self.register_type, self.ssh_key, self.share))
        return result

    def add_to_db(self, register_type, ssh_key, share):
        self.ssh_key = ssh_key
        self.register_type = register_type
        self.share = share
        if register_type == "ui":
           status = "Active"
        else:
           status = "Registered"
        query = "insert into clients (name,ssh_key,status,joindate,share,threshold) values ('%s','%s','%s',%d,'%s',0)" % (self.client, ssh_key, status, int(time.time()), share)
        #print (query)
        query_db(query)
        get_db().commit()
        return "<br>Client %s added to DB, status %s" % ( self.client, status )
    
    def add_to_keyfile(self, authkeyfile, ssh_key):
        self.ssh_key = ssh_key
        self.authkeyfile = authkeyfile
        print (ssh_key)
        auth_command = 'command="/usr/bin/unison -server"'
        with open (authkeyfile, 'a') as f:
          f.write("\n" + auth_command + " " + ssh_key + " CLIENT:%s" % self.client)
        return "<br>Client %s added to Authorized Keys" % self.client

    def activate(self, ssh_key, authkeyfile):
        self.ssh_key = ssh_key
        self.authkeyfile = authkeyfile
        result=[]
        query = ("update clients set status='Active' where name='%s'") % self.client
        query_db(query)
        get_db().commit()
        result = [ "<br>Client activated on database" ]
        result.append(self.add_to_keyfile(self.authkeyfile, self.ssh_key))
        return result

    def set_threshold(self, threshold):
        self.threshold = threshold
        #print (threshold)
        query = ("update clients set threshold=%d where name='%s'") % (self.threshold, self.client)
        query_db(query)
        get_db().commit()
        return "<br>Client %s threshold set to %d seconds" % (self.client, int(threshold))

    def sync_status(self, type='db'):
        self.type = type
        if self.type == "db":
            query = "select sync_status from clients where name='%s';" % self.client
            status = query_db(query)
            return status[0][0]
        else:
           query = "select threshold from clients where name='%s';" % self.client
           threshold = query_db(query)
           query = "select max(id),end_ts from events where client='%s' and status='OK';" % self.client
           lastok = query_db(query)
           if lastok[0][0]:
              ts = int(time.time())
              delta = ts - lastok[0][1]
              if delta <= threshold[0][0]:
                return "In Sync"
              else:
                return "Out of Sync"
           else:
              return "Never synced"

           #print ("Delta is %d, Lastok: %d, Threshold is %d," % ( delta, lastok[0][0], threshold[0][0] ))
           #print ("LastOk %d" % lastok[0][0])
           #print ("Threshold %d" % threshold[0][0])


    def update_sync_status(self, s_status):
        #print(self.client)
        self.s_status = s_status
        current_status = self.sync_status()
        #print(current_status)
        if current_status != s_status:
           query = ("update clients set sync_status='%s' where name='%s'") % (self.s_status, self.client)
           #print(query)
           query_db(query)
           get_db().commit()

    def check_pending(self):
        query="select * from events where client='%s' and status='SYNCING';" % self.client
        brokensync=query_db(query)
        #print("Brokensync :%s" % brokensync)
        if brokensync != []:
           query="update events set status='KO', log='Sync was interrupted' where client='%s' and status='SYNCING';" % self.client
           query_db(query)
           get_db().commit()

    def remove(self, authkeyfile):
        self.authkeyfile = authkeyfile
        query = "delete from clients where name = '%s'" % self.client
        query_db(query)
        get_db().commit()
        with open(authkeyfile, "r+") as f:
            new_f = f.readlines()
            f.seek(0)
            for line in new_f:
                if "CLIENT:%s" % self.client not in line:
                    f.write(line)
            f.truncate()

    def list_clients(self):
        query = "select name from clients"
        clientlist = query_db(query)
        return clientlist

    def list_clients_threshold(self):
        query = "select name from clients where threshold !=0"
        clientlist = query_db(query)
        return clientlist