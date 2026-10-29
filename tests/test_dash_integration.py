"""Component transport authentication and parent/child policy boundaries."""
import os
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from stellis import auth
from stellis.server import app

class DashIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'STELLIS_DATA_DIR':self.temp.name,'STELLIS_AUTH_MODE':'required','STELLIS_DEV_ACCOUNT':'0'})
        self.env.start();self.client=TestClient(app,follow_redirects=False)
    def tearDown(self):
        self.client.close();self.env.stop();self.temp.cleanup()
    def test_visible_source_search_and_stale_time_window(self):
        from stellis.dash_measurements import table_data, content
        measure={'key':'pulse','unit':'bpm','points':[]}
        point={'id':'manual','time':'1999-01-01','value':72,'value2':None,'source':'self','title':'You'}
        self.assertEqual(len(table_data(measure,[point],'Manual entry')['body']),1)
        photo={**point,'id':'photo','title':'Source photo','document_id':'photo-record'}
        self.assertEqual(table_data(measure,[photo],'Source photo')['body'][0][-2],'Source photo')
        empty=content({**measure,'points':[point]},False,'31',True,'2026-10-24')
        self.assertEqual(empty.children,'No readings in this period')

    def test_longitudinal_labels_keep_source_years_and_use_the_shared_widget_palette(self):
        from stellis.dash_measurements import chart
        palette={'ink':'#2C2234','muted':'#5C4F66','line':'#D7D7D4','series':'#335C63'}
        points=[{'time':day,'value':value} for day,value in [('2022-01-21',11),('2025-10-10',42),('2026-06-15',13)]]
        figure=chart({'key':'ferritin','label':'Ferritin','unit':'µg/L','decimals':1},points,True,palette).children[0].figure
        self.assertEqual(figure['layout']['xaxis']['ticktext'],['Jan 2022','Oct 2025','Jun 2026'])
        self.assertEqual(figure['layout']['font']['color'],palette['ink'])
        # Jan and May 2022 sit four months apart on a four-year axis: only the first keeps a label.
        close=[{'time':day,'value':value} for day,value in [('2022-01-21',11),('2022-05-20',34),('2026-06-15',13)]]
        figure=chart({'key':'ferritin','label':'Ferritin','unit':'µg/L','decimals':1},close,True,palette).children[0].figure
        self.assertEqual(figure['layout']['xaxis']['ticktext'],['Jan 2022','Jun 2026'])
        self.assertEqual(len(figure['data'][0]['x']),3)

    def test_dense_series_show_dots_under_a_running_average(self):
        from stellis.dash_measurements import chart
        palette={'ink':'#2C2234','muted':'#5C4F66','line':'#D7D7D4','series':'#335C63'}
        points=[{'time':f'2024-{1+i//28:02d}-{1+i%28:02d}','value':7+(i%3)*.4} for i in range(200)]
        figure=chart({'key':'sleep','label':'Sleep','unit':'hours','decimals':1},points,False,palette).children[0].figure
        self.assertEqual(figure['data'][0]['mode'],'markers')
        self.assertEqual(figure['data'][1]['name'],'7-reading average')
        self.assertEqual(len(figure['data'][1]['y']),200)
        few=chart({'key':'sleep','label':'Sleep','unit':'hours','decimals':1},points[:20],False,palette).children[0].figure
        self.assertEqual(few['data'][0]['mode'],'lines+markers')

    def test_unauthenticated_transports_do_not_enter_component(self):
        self.assertEqual(self.client.get('/components/measurements/_dash-layout').status_code,303)
        self.assertEqual(self.client.post('/components/measurements/_dash-update-component',json={}).status_code,303)
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect('/components/measurements/_dash-ws-callback',headers={'origin':'http://testserver'}):pass
    def test_authenticated_component_and_native_unknown_routes(self):
        self.client.post('/api/v1/auth/register',json={'username':'MayaTest','password':'correct horse battery staple'})
        account=auth.current_account_id(self.client.cookies.get(auth.COOKIE_NAME));auth.complete_onboarding(account)
        self.assertEqual(self.client.get('/components/measurements/_dash-layout').status_code,200)
        with self.client.websocket_connect('/components/measurements/_dash-ws-callback',headers={'origin':'http://testserver'}):pass
        with TestClient(app) as running:
            self.assertEqual(running.post('/api/v1/auth/recover',json={}).status_code,404)

    def test_component_policy_is_contained_and_callbacks_are_stateless(self):
        with patch.dict(os.environ,{'STELLIS_AUTH_MODE':'disabled'}):
            parent=self.client.get('/')
            child=self.client.get('/components/measurements/')
            self.assertEqual(child.status_code,200)
            self.assertNotIn("style-src 'self' 'unsafe-inline'",parent.headers['content-security-policy'])
            self.assertIn("style-src 'self' 'unsafe-inline'",child.headers['content-security-policy'])
            self.assertIn("frame-ancestors 'self'",child.headers['content-security-policy'])
            self.assertIn("frame-ancestors 'none'",parent.headers['content-security-policy'])
            self.assertNotIn("'unsafe-eval'",child.headers['content-security-policy'])
            layout=self.client.get('/components/measurements/_dash-layout').text
            self.assertNotIn('Maya grows with Stellis',layout)
            self.assertIn('measure-data',layout)
            self.assertEqual(child.headers['cache-control'],'no-store')
            with self.assertRaises(WebSocketDisconnect):
                with self.client.websocket_connect('/components/measurements/_dash-ws-callback',headers={'origin':'https://unrelated.example'}):pass

if __name__=='__main__':unittest.main()
