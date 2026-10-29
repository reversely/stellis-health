"""App-owned Dash measurement body; all workspace IO uses the existing browser API."""
import datetime as dt
from pathlib import Path
import dash_mantine_components as dmc
from dash import Dash, Input, Output, State, MATCH, ClientsideFunction, dcc, html, no_update
SOURCE_LABELS={"self":"Manual entry","camera":"Camera reading","record":"From records"}

def source_label(point):
    return (point.get('title') or SOURCE_LABELS['record']) if point.get('source','record')=='record' or point.get('document_id') else SOURCE_LABELS[point['source']]


def table_data(measure, points, query="", order="newest"):
    columns = ["Date", f"Reading ({measure['unit']})"]
    paired = measure.get("paired", False)
    if paired:
        columns = ["Date", "Systolic (mmHg)", "Diastolic (mmHg)"]
    columns.extend(["Source","Action"])
    filtered = [p for p in points if query.lower() in " ".join(str(v) for v in [p["time"], p["value"], p.get("value2", ""), source_label(p)]).lower()]
    filtered.sort(key=lambda p: p["value"] if order in {"highest", "lowest"} else p["time"], reverse=order in {"newest", "highest"})
    rows = [[p["time"], p["value"], *([p["value2"]] if paired else []), source_label(p)] for p in filtered]
    for row,point in zip(rows,filtered):
        row.append(dmc.Button('Delete',variant='subtle',size='compact-md',**{'data-measure-action':'delete','data-reading-id':point['id']}) if point['source']!='record' else html.A('Original report',href=point['target'],**{'data-measure-source':'true'}))
    return {"head": columns, "body": rows}


def table_elements(data):
    return [dmc.TableThead(dmc.TableTr([dmc.TableTh(cell) for cell in data['head']])),dmc.TableTbody([dmc.TableTr([dmc.TableTd(cell) for cell in row]) for row in data['body']])]


def grid(measure, points):
    """Mantine's data-driven table, reused across all measurement schemas."""
    key = measure["key"]
    return dmc.Stack([
        dcc.Store(id={"kind": "table-points", "key": key}, data=points),
        dmc.Group([
            dmc.TextInput(id={"kind": "filter", "key": key}, label="Search readings", placeholder="Date, value or source", debounce=200),
            dmc.Select(id={"kind": "order", "key": key}, label="Order", value="newest", allowDeselect=False,
                       data=[{"label": label, "value": value} for label, value in [("Newest first", "newest"), ("Oldest first", "oldest"), ("Highest first", "highest"), ("Lowest first", "lowest")]]),
        ], grow=True, wrap="nowrap", className="preview-table-tools"),
        dmc.TableScrollContainer(dmc.Table(id={"kind": "readings-table", "key": key}, children=table_elements(table_data(measure, points)),
                                          highlightOnHover=True, horizontalSpacing="sm", verticalSpacing="sm", stickyHeader=True),
                                 minWidth=420 if measure.get("paired", False) else 300, maxHeight=220, type="native"),
    ], gap="sm", mt="md", className="preview-grid")


# Above this many readings a chart shows dots and a running average instead of a joined line.
DENSE = 120


def chart(measure, points, dark, palette=None):
    if not palette:
        return dmc.Text('Loading chart',c='dimmed')
    ink, muted, line, series = [palette[key] for key in ('ink','muted','line','series')]
    traces = [dict(x=[p["time"] for p in points], y=[p["value"] for p in points],
                   type="scatter", mode="lines+markers+text" if len(points)<=6 else "lines+markers", text=[f"{p['value']:g}" for p in points], textposition="top center", name="Systolic" if measure.get("paired") else measure["label"],
                   line={"color": series, "width": 2}, marker={"size": 6},
                   hoverinfo="none", hovertemplate=None,
                   customdata=[[dt.date.fromisoformat(p["time"][:10]).strftime("%d %b %Y"), f"{p['value']:.{measure['decimals']}f} {measure['unit']}", source_label(p), p.get("target")] for p in points])]
    if len(points) > DENSE and not measure.get("paired", False):
        # Hundreds of daily readings joined by a line read as a scribble. Each reading stays as a
        # faint dot (with its tooltip) under a 7-reading average that shows the trend.
        traces[0].update(mode="markers", marker={"size": 4, "color": series, "opacity": .35})
        values = [p["value"] for p in points]
        average = [sum(values[max(0, i - 3):i + 4]) / len(values[max(0, i - 3):i + 4]) for i in range(len(values))]
        traces.append(dict(x=[p["time"] for p in points], y=average, type="scatter", mode="lines", name="7-reading average",
                           line={"color": series, "width": 2.5}, hoverinfo="skip"))
    if measure.get("paired", False):
        traces.append(dict(x=[p["time"] for p in points], y=[p["value2"] for p in points],
                           type="scatter", mode="lines+markers", name="Diastolic", line={"color": series, "dash": "dot"}, hoverinfo="none", hovertemplate=None,
                           customdata=[[dt.date.fromisoformat(p["time"][:10]).strftime("%d %b %Y"), f"{p['value2']:.0f} {measure['unit']}"] for p in points]))
    usual = measure.get("usual")
    shapes = []
    if usual and not measure.get("paired", False):
        shapes.append(dict(type="rect", xref="paper", x0=0, x1=1, y0=usual["low"], y1=usual["high"],
                           fillcolor=series, opacity=.12, line={"width": 0}, layer="below"))
    figure = {"data": traces, "layout": dict(
        template="none", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Inter, sans-serif", "color": ink, "size": 12},
        margin={"l": 40, "r": 12, "t": 18, "b": 40}, height=240,
        # Fixed ranges: a press on an axis end would otherwise open Plotly's range editor.
        xaxis={"type": "date", "showgrid": False, "tickformat": "%d %b", "tickfont": {"color": muted}, "nticks": 4, "fixedrange": True},
        yaxis={"gridcolor": line, "zeroline": False, "tickfont": {"color": muted}, "nticks": 4, "fixedrange": True},
        hovermode="closest", dragmode=False,
        showlegend=measure.get("paired", False), legend={"orientation": "h", "y": 1.15}, shapes=shapes,
    )}
    dates = sorted({p['time'][:10] for p in points})
    across_years = dates[0][:4] != dates[-1][:4]
    if len(dates) <= 6:
        # A label closer than an eighth of the span to the one before would print over it; its point stays.
        ordinal = [dt.date.fromisoformat(day).toordinal() for day in dates]
        gap = (ordinal[-1] - ordinal[0]) / 8
        shown = [0]
        for k in range(1, len(dates)):
            if ordinal[k] - ordinal[shown[-1]] >= gap:
                shown.append(k)
        figure['layout']['xaxis'].update(tickmode='array', tickvals=[dates[k] for k in shown], automargin=True,
            tickangle=-30 if across_years else 0,
            ticktext=[dt.date.fromisoformat(dates[k]).strftime('%b %Y' if across_years else '%d %b') for k in shown])
    elif across_years:
        figure['layout']['xaxis']['tickformat']='%b %Y'
    # Avoid exaggerating small day-to-day changes, matching the production charts.
    span = {"pulse": 30, "breathing_rate": 8, "hrv": 20, "weight": 6, "blood_glucose": 30, "blood_pressure": 60}.get(measure["key"], max(1, max(p["value"] for p in points)*.1))
    values = [p["value"] for p in points] + [p["value2"] for p in points if p.get("value2") is not None]
    centre = (min(values) + max(values)) / 2
    half = max(span, max(values) - min(values)) * .6
    figure["layout"]["yaxis"]["range"] = [centre - half, centre + half]
    children = [
        dcc.Graph(id={"kind": "graph", "key": measure["key"]}, figure=figure, responsive=True, clear_on_unhover=True,
                  config={"displayModeBar": False, "scrollZoom": False}, style={"height": 240}),
        dcc.Tooltip(id={"kind": "reading-tooltip", "key": measure["key"]}, show=False,
                    background_color="#44364E", border_color="#44364E", className="preview-reading-tooltip"),
    ]
    if shapes:
        bounds = f"{usual['low']:.{measure['decimals']}f}–{usual['high']:.{measure['decimals']}f} {measure['unit']}"
        children.append(dmc.Group([html.Span(className="preview-range-key", **{"aria-hidden": "true"}),
                                   dmc.Text(f"Usual range {bounds}", size="xs", c="dimmed")], gap=8, className="preview-range-legend"))
    return html.Div(children, className="preview-plot")


def content(measure, table, period, dark, today=None, palette=None):
    points = measure.get("entries",measure["points"]) if table else measure["points"]
    if period != "all":
        cutoff = ((dt.date.fromisoformat(today) if today else dt.date.today()) - dt.timedelta(days=int(period) - 1)).isoformat()
        points = [p for p in points if p["time"] >= cutoff]
    if not points:return dmc.Text("No readings in this period",c="dimmed",py="lg")
    return grid(measure, points) if table else chart(measure, points, dark, palette)


def reading_head(measure):
    last=measure['points'][-1]
    value=f"{last['value']:.{measure['decimals']}f}"
    if last.get('value2') is not None:value+=f"/{last['value2']:.0f}"
    return [
        dmc.Text('Latest result',c='dimmed'),
        dmc.Group([dmc.Text(value,className='preview-value'),dmc.Text(measure['unit'],c='dimmed')],gap=6,align='baseline'),
        # The source is a link to its report (owner, 2026-10-28: report references open their source).
        dmc.Group([html.Time(dt.date.fromisoformat(last['time'][:10]).strftime('%d %b %Y'),dateTime=last['time']),
                   html.A(source_label(last),href=last['target'],className='preview-source',**{'data-measure-source':'true'}) if last.get('target') and last.get('source','record')=='record'
                   else dmc.Text(source_label(last),size='sm',c='dimmed')],gap=12,className='preview-meta'),
    ]


def card(measure):
    return dmc.Paper([
        dmc.Group([
            dmc.Text(measure['label'],fw=600,c='dimmed',size='sm'),
            dmc.Group([
                # Expands the chart in place to the full row (owner, 2026-10-28); bridge.js toggles it.
                dmc.ActionIcon(html.Span(className='expand-view-glyph',**{'aria-hidden':'true'}),variant='subtle',size=44,className='preview-expand-control',**{'aria-label':'Expand chart','aria-pressed':'false','data-measure-expand':'true'}),
                dmc.ActionIcon(html.Span(className='table-view-glyph',**{'aria-hidden':'true'}),id={'kind':'table','key':measure['key']},n_clicks=0,variant='subtle',size=44,className='preview-table-control',**{'aria-label':'Table view','aria-pressed':'false'}),
            ],gap=4,wrap='nowrap'),
        ],justify='space-between',wrap='nowrap',mih=44),
        html.Div(reading_head(measure),id={'kind':'reading-head','key':measure['key']}),
        html.Div(id={'kind':'view','key':measure['key']}),
    ],withBorder=True,radius='sm',p='lg',className='preview-card widget-paper')



def install(server):
    """Mount a stateless HTTP-callback component behind the app's session gate."""
    server.add_middleware(ComponentSocketGate)
    app = Dash(server=server,url_base_pathname='/components/measurements/',
               assets_folder=str(Path(__file__).parent/'static/dash-measurements'),
               websocket_callbacks=False,suppress_callback_exceptions=True,title='Measurements')
    # Dash 4.4.1 otherwise adds a root catch-all at ASGI startup. This body
    # has an explicit prefixed index; unknown routes belong to the native app.
    # Keep the version pinned and regression-check native 404/auth behavior.
    app.backend._setup_catchall=lambda: None
    app.layout=dmc.MantineProvider(id='measure-theme',forceColorScheme='dark',
        theme={'fontFamily':'Inter, sans-serif','headings':{'fontFamily':'Piazzolla, Georgia, serif','fontWeight':'300'},'primaryColor':'dark','colors':{'dark':['#F9F5FC','#DED5DE','#C4B6C6','#6E5B79','#51415C','#44364E','#382C42','#302638','#2C2234','#2C2234']}},children=html.Main([
        dcc.Store(id='measure-data',data=[]),dcc.Store(id='measure-layout-key',data=None),dcc.Store(id='measure-category',data='all'),dcc.Store(id='measure-palette',data=None),dcc.Store(id='measure-today',data=dt.date.today().isoformat()),
        dcc.Interval(id='measure-bridge',interval=500),html.Div(id='bridge-status',hidden=True),
        dmc.Group([dmc.Title('Measurements',order=2),dmc.SegmentedControl(id='measure-range',data=[{'label':n,'value':v} for n,v in [('1 month','31'),('3 months','92'),('1 year','366'),('All','all')]],value='all')],justify='space-between',className='measure-heading'),
        dmc.Select(id='measure-category-select',label='Measurement',data=[{'label':'All measurements','value':'all'}],value='all',allowDeselect=False,mb='lg'),
        html.Div(id='measure-panels'),html.P(id='measure-error',role='status'),
    ],className='measurements-body'))

    app.clientside_callback(ClientsideFunction(namespace='stellisMeasurements',function_name='bridge'),Output('bridge-status','children'),Input('measure-bridge','n_intervals'))

    @app.callback(Output('measure-panels','children'),Output('measure-layout-key','data'),Output('measure-range','style'),Output('measure-category-select','style'),Input('measure-data','data'),Input('measure-category-select','value'),State('measure-layout-key','data'))
    def panels(measures,selected,last_layout):
        data={m['key']:m for m in measures if m.get('points')}
        keys=list(data) if selected in (None,'all') or selected not in data else [selected]
        layout=str(selected)+':'+','.join(keys)
        if layout==last_layout:return no_update,no_update,no_update,no_update
        # No readings: no range, picker or empty line; the page keeps only its Log actions (owner, 2026-10-28).
        hide={'display':'none'} if not keys else {}
        if not keys:return [],layout,hide,hide
        return dmc.SimpleGrid([card(data[k]) for k in keys],cols={'base':1,'sm':2,'xl':3},spacing='lg'),layout,hide,hide

    @app.callback(Output({'kind':'view','key':MATCH},'children'),Output({'kind':'table','key':MATCH},'aria-pressed'),Output({'kind':'reading-head','key':MATCH},'children'),Input({'kind':'table','key':MATCH},'n_clicks'),Input('measure-range','value'),Input('measure-theme','forceColorScheme'),Input('measure-today','data'),Input('measure-data','data'),Input('measure-palette','data'),State({'kind':'table','key':MATCH},'id'))
    def render(clicks,period,theme,today,data,palette,identity):
        measure=next(m for m in data if m['key']==identity['key'])
        if not measure['points']:return [],'false',[]
        body=content(measure,bool((clicks or 0)%2),period,theme=='dark',today,palette)
        # Sources remain real app routes, opened by the parent navigation shell.
        return body,str(bool((clicks or 0)%2)).lower(),reading_head(measure)

    @app.callback(Output({'kind':'readings-table','key':MATCH},'children'),Input({'kind':'filter','key':MATCH},'value'),Input({'kind':'order','key':MATCH},'value'),State({'kind':'table-points','key':MATCH},'data'),State({'kind':'filter','key':MATCH},'id'),State('measure-data','data'))
    def filtered(query,order,points,identity,data):
        return table_elements(table_data(next(m for m in data if m['key']==identity['key']),points,query or '',order))

    app.clientside_callback(ClientsideFunction(namespace='stellisPreview',function_name='readingTooltip'),Output({'kind':'reading-tooltip','key':MATCH},'show'),Output({'kind':'reading-tooltip','key':MATCH},'bbox'),Output({'kind':'reading-tooltip','key':MATCH},'children'),Output({'kind':'reading-tooltip','key':MATCH},'direction'),Input({'kind':'graph','key':MATCH},'hoverData'))
    return app

class ComponentSocketGate:
    """Apply the account/session and same-origin rules to Dash's runtime socket."""
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']=='websocket' and scope.get('path','').startswith('/components/measurements/'):
            import os
            from starlette.requests import HTTPConnection
            from stellis import auth
            request=HTTPConnection(scope);origin=request.headers.get('origin');token=request.cookies.get(auth.COOKIE_NAME)
            bound=os.environ.get('STELLIS_ACCOUNT_ID');account=auth.current_account_id(token) if auth.enabled() else None
            dev=not bound and auth.dev_enabled() and auth.current_account(token)=='dev' if auth.enabled() else False
            if (origin and origin.split('://',1)[-1]!=request.headers.get('host')) or (auth.enabled() and ((account is None and not dev) or (bound and account!=int(bound)))):
                await send({'type':'websocket.close','code':4401});return
        await self.app(scope,receive,send)
