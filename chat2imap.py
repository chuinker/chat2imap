#!/usr/bin/env python3
'''
Convert Pidgin/Adium chat logs to IMAP messages
Requires pytz and BeautifulSoup 4
'''

import os
import sys
import imaplib
import re
from argparse import ArgumentParser
from configparser import ConfigParser
from urllib.parse import unquote
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.parser import HeaderParser
from bs4 import BeautifulSoup as b4parser
from pytz import timezone


class LogDir(object):
    '''
    class for containing everything knowable about a chat from the directory that contained the file
    '''

    def __init__(self,dir_name):
        '''
        extract protocol and account information from the directory name
        '''
        self.dir_name = dir_name
        dir_parts = dir_name.split(os.sep)
        if dir_parts[-1].endswith('.chatlog'):
            self.log_type = 'adium'
            (self.protocol,self.account) = dir_parts[-3].split('.',1)
            self.protocol = self.protocol.lower().rstrip('!')
            self.contact = dir_parts[-2]
        else:
            self.log_type = 'pidgin'
            (self.protocol, self.account, self.contact) = dir_parts[-3:]

        self.from_email = self.get_from_email()
        self.to_email = self.get_to_email()

    def get_from_email(self):
        '''
        Try to coerce the contact name into a valid email address
        '''
        contact = unquote(self.contact).replace(' ','_')
        if self.protocol == 'aim':
            return '{}@aol.com'.format(contact)
        if self.protocol == 'facebook':
            return '{}@facebook.com'.format(contact)
        if self.protocol == 'irc':
            return '{}@irc'.format(contact.replace('#',''))
        if self.protocol in ['jabber','gtalk']:
            return contact
        if self.protocol == 'msn':
            if '@' in contact:
                return contact
            return '{}@hotmail.com'.format(contact)
        if self.protocol == 'yahoo':
            return '{}@yahoo.com'.format(contact)
        return '{}@{}'.format(contact,self.protocol)

    def get_to_email(self):
        '''
        Try to coerce the account name into a valid email address
        '''
        if self.protocol in ['facebook','irc','jabber','gtalk']:
            return self.account
        return self.get_from_email()



class LogFile(object):
    '''
    class for containing everything knowable about a chat from the file itself, with a LogDir for context
    '''
    def __init__(self,log_dir,base_name,local_timezone):
        self.log_dir = log_dir
        self.base_name = base_name
        self.root_name, self.extension = os.path.splitext(base_name)
        self.file_name = os.path.join(log_dir.dir_name,base_name)
        self.timestamp = self.get_timestamp(local_timezone)
        self.message_id = '{}_{}'.format(self.timestamp.strftime('%Y%m%d%H%M%S%z'),log_dir.from_email)
        self.file_mtime = self.get_mtime(local_timezone)

    def get_mtime(self,local_timezone):
        '''
        return the file modification time as a tz-aware date string
        '''
        return local_timezone.localize(datetime.fromtimestamp(os.path.getmtime(self.file_name))).strftime('%Y%m%d%H%M%S%z')

    def get_timestamp(self,local_timezone):
        '''
        Try to get a timestamp from the filename itself. If that doesn't work, go with the timestamp on the file descriptor

        I had to abandon python2 because it didn't support %z

        This function has shaken my faith in python as a language written by adults.
        '''
        # strip off the extension, strip off any 3-letter timezone code
        try:
            if self.extension == '.xml':
                # filename will be like guy@someplace.ext (2014-09-04T15.45.41-0400).xml
                return datetime.strptime(self.root_name.split(' ')[-1],'(%Y-%m-%dT%H.%M.%S%z)')
            else:
                # whole filename will be like: 2014-09-04.15.45.41-0400EDT.html, 2014-09-04.15.45.41-0400.html
                #   or 2014-09-04.15.45.41.html
                # strip off the EST/CDT, etc, because not even python 3 can withstand timezone codes
                ts_string = re.sub('[A-Z]{2}T$','',self.root_name)
                try:
                    return datetime.strptime(ts_string,'%Y-%m-%d.%H%M%S%z')
                except:
                    return local_timezone.localize(datetime.strptime(ts_string,'%Y-%m-%d.%H%M%S'))
        except:
            print('timezone parsing failure: ',self.file_name,file=sys.stderr)
            raise


    def get_text(self,text_encodings):
        '''
        Read the text of the file and strip away all the goo, like trailing whitespace and null characters
        '''
        for enc in text_encodings:
            try:
                # read the file with the given encoding, remove junk whitespace and NULspace at the end
                with open(self.file_name,'r', encoding=enc) as f:
                    return f.read().rstrip(' \t\r\n\0')
            except Exception as e:
                print('encoding ',enc,' failed for ',self.file_name,': ',e,file=sys.stderr)
                continue
        raise ValueError('Tried All Encodings for ',self.file_name)


    def create_email(self,text_encodings):
        '''
        form the email as a string
        '''
        email = MIMEMultipart()
        email['Subject'] = 'chat with {}'.format(self.log_dir.from_email)
        email['To'] = self.log_dir.to_email
        email['From'] = self.log_dir.from_email
        email.add_header('Message-ID',self.message_id)
        # these *should* help the IMAP server organize all chats with a person into one thread
        references = 'Chats between {} and {}'.format(self.log_dir.from_email,self.log_dir.to_email)
        email.add_header('References',references)
        email.add_header('In-Reply-To',references)
        # helpful debug info
        email.add_header('X-Creation-Date',datetime.now().strftime('%Y-%m-%d %H:%M:%S%z'))
        email.add_header('X-Creator','chat2imap.py v0.1')
        email.add_header('X-Source-File', self.file_name)
        email.add_header('X-Source-File-ModifiedTime', self.file_mtime)

        chat_text = self.get_text(text_encodings)
        if self.extension == '.html':
            images = {}
            html_doc = b4parser(chat_text)
            for img_tag in html_doc.findAll('img'):
                if 'src' in img_tag.attrs:
                    img_name = img_tag['src']
                    img_tag['src'] = 'cid:' + img_tag['src']
                    try:
                        with open(os.path.join(self.log_dir.dir_name,img_name),'rb') as i:
                            mime_image = MIMEImage(i.read())
                            mime_image.add_header('Content-ID','<{}>'.format(img_name))
                            images[img_name] = mime_image
                    except (FileNotFoundError, IOError):
                        # leave it as a broken link with no attachment
                        pass

            email.attach(MIMEText(html_doc.prettify(),'html'))
            for i in images.values():
                email.attach(mime_image)
        elif self.extension == '.xml':
            email_text = ''
            xmldoc = b4parser(chat_text,'xml')
            for chat in xmldoc.find_all('chat'):
                for tag in chat.children:
                    if hasattr(tag,'name') and hasattr(tag,'get'):
                        ts = tag.get('time') or '(unknown time)'
                        sender = tag.get('sender')
                        alias = tag.get('alias')
                        text = tag.get_text()
                        if alias:
                            sender = '{}({})'.format(sender,alias)
                        if tag.name == 'message':
                            email_text += '{} {}: {}\n'.format(ts,sender,text)
                        elif tag.name in ['event','chat']:
                            pass
                        elif tag.name == 'status':
                            email_text += '[{} {} - {}]]\n'.format(ts,sender,text)
                        else:
                            raise ValueError('Unexpected tag: {}'.format(tag.name))
            email.attach(MIMEText(email_text))
        else:
            email.attach(MIMEText(chat_text))

        return email.as_string().encode()

class IMAPServer(object):
    '''
    class to encapsulate every interacton with an imap server
    '''
    def __init__(self,host,username,password,folder,message_flags):
        self.server = imaplib.IMAP4_SSL(host)
        self.server.login(username,password)
        self.server.create(folder)
        self.folder = folder
        self.message_flags = message_flags
        self.existing_message_ids = { x:y for (x,y) in self.get_existing_message_ids() }
        self.num_stored_messages = len(self.existing_message_ids)

    def get_existing_message_ids(self):
        '''
        Fetch a list of messages from the server in the current folder
        They are in a record pair format like
        rec0: (b'1 (BODY[HEADER.FIELDS (MESSAGE-ID)] {49}', b'Message-ID: 20080130012511-0500_somebody@somewhere\r\n\r\n')
        rec1: b')'
        '''
        header_parser = HeaderParser()
        self.server.select(self.folder)
        imap_response, message_list = self.server.fetch('1:*','(UID BODY[HEADER.FIELDS (Message-ID X-Source-File-Modifiedtime)])')

         # every second element contains b')' and should be discarded
        for message in message_list[::2]:
            index_bytes,header_bytes = message
            # index bytes is like this: b'1 (UID 55228 BODY[HEADER.FIELDS (Message-ID X-Source-File-Modifiedtime)] {55}'
            uid = index_bytes.decode('utf-8').split(maxsplit=3)[2]
            headers = header_parser.parsestr(header_bytes.decode('utf-8'))
            message_id = headers.get('Message-ID',None)
            mtime = headers.get('X-Source-File-Modifiedtime',None)
            if not mtime:
                print('uid ', uid, ' message-id ', message_id,' has no mtime, deleted',file=sys.stderr)
                self.delete(uid)
            elif message_id:
                yield message_id, (uid, mtime)
                

    def store(self,email_as_string,timestamp,message_id):
        '''
        push this message up to the server and remember that it was seen
        '''
        self.existing_message_ids[message_id] = (None,timestamp)
        try:
            self.server.append(self.folder,self.message_flags,imaplib.Time2Internaldate(timestamp),email_as_string)
        except:
            raise

    def delete(self,uid):
        '''
        Delete a message
        '''
        self.server.uid('STORE',uid,'+FLAGS','(\Deleted)')
        self.server.expunge()

class Configuration(object):
    '''
    container for all values to be extracted from configuration, and the means of calculating them
    '''

    def qualified_account_name(protocol,account):
        '''
        Account names as they should be expressed in the config file protocol:acctname
        '''
        return '{}:{}'.format(protocol,account)

    def qualified_contact_name(protocol,account,contact):
        '''
        Account names as they should be expressed in the config file protocol:acctname
        '''
        return ':'.join([protocol,account,contact])


    def __init__(self, config_file=None):
        config_parser = ConfigParser()
        if not config_file:
            config_file = os.path.join('~','.chat2imap.conf')
        if not config_parser.read(os.path.expanduser(config_file)):
            raise IOError('file not found: ' + config_file)

        self.known_chat_types = ['aim','facebook','irc','jabber','msn','yahoo','gtalk']
        self.known_logfile_extensions = ['.html','.txt','.xml']

        self.local_timezone = timezone(config_parser.get('DEFAULT','LocalTimeZone',fallback='US/Eastern'))

        self.imap_folder = config_parser.get('IMAP','Folder',fallback='ChatLog')
        self.imap_host = config_parser.get('IMAP','HostName')
        self.imap_username = config_parser.get('IMAP','UserName')
        self.imap_password = config_parser.get('IMAP','Password')
        if config_parser.getboolean('DEFAULT','MarkMessagesAsSeen',fallback=False):
            self.imap_flags = '\Seen'
        else:
            self.imap_flags = ''

        #self.text_encodings = [None] + config_parser.get('DEFAULT','TextEncodings',fallback='utf-8').split(',')
        self.text_encodings = config_parser.get('DEFAULT','TextEncodings',fallback='utf-8').split(',')
        self.accounts_to_skip = set()
        for entry in config_parser.get('DEFAULT','AccountsToSkip',fallback='').split(','):
            (protocol,account) = entry.strip(' \t\r\n').split(':',1)
            self.accounts_to_skip.add(Configuration.qualified_account_name(protocol.strip(' \t\r\n'), account.strip(' \t\r\n')))

        self.contacts_to_skip = set()
        for entry in config_parser.get('DEFAULT','ContactsToSkip',fallback='').split(','):
            (protocol,account,contact) = entry.strip(' \t\r\n').split(':',2)
            self.contacts_to_skip.add(Configuration.qualified_contact_name(protocol,account,contact))

        self.base_log_dirs = []
        for l in config_parser.get('DEFAULT','LogDirs',fallback=os.path.join('~','.chat','logs')).split(','):
            log_dir = os.path.expanduser(l.strip())
            if os.path.exists(log_dir):
                self.base_log_dirs.append(log_dir)
            else:
                print('log dir ',log_dir,' does not exist',file=sys.stderr)

    def syncable_protocol(self,protocol):
        '''
        return true if this is a protocol that we can synchronize
        '''
        return protocol in self.known_chat_types

    def syncable_account(self,protocol,account):
        '''
        return true if this is a account that we can synchronize
        '''
        return self.syncable_protocol(protocol) and \
            Configuration.qualified_account_name(protocol,account) not in self.accounts_to_skip

    def syncable_contact(self,protocol,account,contact):
        '''
        return true if this is a contact that we can synchronize
        '''
        return self.syncable_account(protocol,account) and \
            Configuration.qualified_contact_name(protocol,account,contact) not in self.contacts_to_skip

    def is_log_file_name(self,file_name):
        '''
        Log files never start with ._ (an adium thing for binary files)
        Log files must have one of the known file extensions.
        '''
        return not file_name.startswith('._') and os.path.splitext(file_name)[1] in self.known_logfile_extensions


def find_log_files(config):
    '''
    Iterate over all log file base dirs, walk each dir looking for valid sync-able log files, and return them as objects
    '''
    for d in config.base_log_dirs:
        for (dirpath, _, filenames) in os.walk(d):
            log_filenames = [ x for x in filenames if config.is_log_file_name(x) ]
            if log_filenames:
                log_dir = LogDir(dirpath)
                if config.syncable_contact(log_dir.protocol,log_dir.account,log_dir.contact):
                    for f in log_filenames:
                        yield LogFile(log_dir,f,config.local_timezone) 

if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--config-file', help='use the config file specified')
    args = parser.parse_args()

    config = Configuration(config_file=args.config_file)
    imap_server = IMAPServer(config.imap_host,config.imap_username,config.imap_password,config.imap_folder,config.imap_flags)
    imap_server.server.expunge()

    for f in find_log_files(config):
        uid, mtime = imap_server.existing_message_ids.get(f.message_id,[None,None])
        if uid:
            # message exists, but might be older than the file
            if mtime and mtime >= f.file_mtime:
                #print(f.file_name, ' mtime ', f.file_mtime, ' means ', mtime, 'is ok')
                # message exists and is up to date
                continue
            else:
                print('message ', f.file_name, ' with uid ', uid,' too old (', mtime, ') deleting.',file=sys.stderr)
                # message is malformed or too old, delete it
                imap_server.delete(uid)
        else:
            print(f.file_name, ' was not found')
        try:
            print('storing ', f.file_name)
            imap_server.store(f.create_email(config.text_encodings),f.timestamp,f.message_id)
        except:
            print('Error storing file ',f.file_name,file=sys.stderr)
            raise



