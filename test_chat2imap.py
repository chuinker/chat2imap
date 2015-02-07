#!/usr/bin/env python3

import unittest

from chat2imap import Configuration, LogDir, LogFile, IMAPServer, find_log_files


class chat2imapTestCase(unittest.TestCase):
    '''
    tests for chat2imap.py
    '''
    def test_config(self):
        with self.assertRaises(IOError):
            Configuration(config_file = '/nonexistent/path')

    def test_all_email_creation(self):
        config = Configuration()
        
        for f in find_log_files(config):
            text = str(f.create_email(config.text_encodings))
            if not 'Message-ID' in text:
                raise AssertionError('{}: {}'.format(f.file_name,text))


if __name__ == '__main__':
    unittest.main()

