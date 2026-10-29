/* Keep hover rendering local so pointer motion never waits on a server callback. */
window.dash_clientside = Object.assign({}, window.dash_clientside, {
  stellisPreview: {
    readingTooltip(data) {
      const unchanged = window.dash_clientside.no_update;
      if (!data?.points?.length) return [false, unchanged, unchanged, unchanged];
      const point = data.points[0];
      const node = (type, props) => ({namespace: 'dash_html_components', type, props});
      const content = node('Div', {className: 'preview-tooltip-content', children: [
        node('Time', {dateTime: point.x, children: point.customdata[0]}),
        node('Div', {className: 'preview-tooltip-reading', children: [
          node('Span', {className: 'preview-tooltip-marker', 'aria-hidden': 'true'}),
          node('Strong', {children: point.customdata[1]})
        ]}),
        ...(point.customdata[2]?[node('Span',{children:point.customdata[2]})]:[])
      ]});
      // The label opens toward the wider side of the chart, so a long source never runs off its edge.
      return [true, point.bbox, content, point.bbox.x0 < window.innerWidth / 2 ? 'right' : 'left'];
    }
  }
});
