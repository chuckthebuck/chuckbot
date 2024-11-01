fixes['Chuckbot'] = {
    'regex': True,
    'recursive': False,
    'nocase': True,
	'msg': {
        '_default':u'Perform category move to fix WLKBot fiasco',
    },
    'replacements': [
		(r'(\[\[category:[^]]+\]\])(.+?) by (.+?) in the Statens Museum for Kunst\]\]',
		r'\1\2 by \3 in Statens Museum for Kunst]]')
    ],
    'exceptions': {
        'inside-tags': [
             'nowiki',
             'comment',
             'math',
             'pre'
        ]
    }
}
