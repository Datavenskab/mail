from email.generator import DecodedGenerator


class DecodingDecodedGenerator(DecodedGenerator):
    '''
    For some reason DecodedGenerator doesn't actually decode the
    Content-Transfer-Encoding.
    This subclass patches _dispatch to actually decode the contents.
    '''

    def _dispatch(self, msg):
        for part in msg.walk():
            maintype = part.get_content_maintype()
            if maintype == 'text':
                bpayload = part.get_payload(decode=True)
                try:
                    payload = bpayload.decode(
                        part.get_param('charset', 'ascii'), 'replace')
                except LookupError:
                    payload = bpayload.decode('ascii', 'replace')
                print(payload, file=self)
            elif maintype == 'multipart':
                pass
            else:
                print(self._fmt % {
                    'type'       : part.get_content_type(),
                    'maintype'   : part.get_content_maintype(),
                    'subtype'    : part.get_content_subtype(),
                    'filename'   : part.get_filename('[no filename]'),
                    'description': part.get('Content-Description',
                                            '[no description]'),
                    'encoding'   : part.get('Content-Transfer-Encoding',
                                            '[no encoding]'),
                    }, file=self)
