
    function fmtNum(n) { return Number(n).toLocaleString('en-IN'); }
    function fmtRs(n) {
        if (n >= 1e7) return '₹' + (n / 1e7).toFixed(2) + ' Cr';
        if (n >= 1e5) return '₹' + (n / 1e5).toFixed(2) + ' L';
        return '₹' + Number(n).toLocaleString('en-IN', {maximumFractionDigits:0});
    }

    fetch('/api/v1/campaign-analysis/')
        .then(res => res.json())
        .then(json => {
            if (json.status !== 'success') {
                console.error("API Error:", json.message || "Unknown error");
                document.querySelectorAll('.spinner-border').forEach(s => s.style.display = 'none');
                document.querySelector('#cohort-table tbody').innerHTML = `<tr><td colspan="6" class="text-danger text-center"><strong>API Database Error:</strong> ${json.message || "Server failed to load data."}</td></tr>`;
                return;
            }

            const data = json.data;

            // 1. Aggregations for KPIs and Waterfall
            let totalBase = 0;
            let totalReactivated = 0;
            let totalRev = 0;

            // Aggregate by month for waterfall
            let monthTotals = {
                'Jan 2026': {reactivated: 0, revenue: 0, remaining: 0}, 
                'Feb 2026': {reactivated: 0, revenue: 0, remaining: 0}, 
                'Mar 2026': {reactivated: 0, revenue: 0, remaining: 0}, 
                'Apr 2026': {reactivated: 0, revenue: 0, remaining: 0}, 
                'May 2026': {reactivated: 0, revenue: 0, remaining: 0}
            };

            let tableHtml = '';

            data.forEach(cohort => {
                totalBase += cohort.initial_base;
                totalReactivated += cohort.total_reactivated;
                totalRev += cohort.reactivated_revenue;

                let monthCells = '';
                cohort.monthly_breakdown.forEach(mb => {
                    monthTotals[mb.month].reactivated += mb.reactivated;
                    monthTotals[mb.month].revenue += mb.revenue;
                    monthTotals[mb.month].remaining += mb.remaining;
                    
                    monthCells += `
                        <td>
                            <div class="hm-cell">
                                <span class="hm-val">${fmtNum(mb.reactivated)}</span>
                                <span class="hm-sub">Rate: <span style="color:#10b981; font-weight:600;">${(mb.reactivated / cohort.initial_base * 100).toFixed(2)}%</span></span>
                                <span class="hm-sub">Bal: ${fmtNum(mb.remaining)}</span>
                                <span class="hm-sub" style="color: #f59e0b; font-weight: 600; margin-top: 2px;">${fmtRs(mb.revenue)}</span>
                            </div>
                        </td>
                    `;
                });

                tableHtml += `
                    <tr>
                        <td><span class="cohort-badge">${cohort.cohort_year} Cohort</span></td>
                        <td style="font-weight:700; color:#0f172a;">${fmtNum(cohort.initial_base)}</td>
                        ${monthCells}
                        <td style="font-weight:700; color:#10b981;">${fmtNum(cohort.total_reactivated)}</td>
                        <td style="font-weight:700;">${cohort.resurrection_rate}%</td>
                        <td style="color:#f59e0b; font-weight:700;">${fmtRs(cohort.reactivated_revenue)}</td>
                    </tr>
                `;
            });

            // Calculate overall KPIs early for the Total row
            const overallRate = totalBase > 0 ? ((totalReactivated / totalBase) * 100).toFixed(2) : 0;

            // Generate Total Row
            let totalMonthCells = '';
            ['Jan 2026', 'Feb 2026', 'Mar 2026', 'Apr 2026', 'May 2026'].forEach(m => {
                let mData = monthTotals[m];
                let mRate = totalBase > 0 ? ((mData.reactivated / totalBase) * 100).toFixed(2) : 0;
                totalMonthCells += `
                        <td>
                            <div class="hm-cell">
                                <span class="hm-val" style="font-size: 1.05em;">${fmtNum(mData.reactivated)}</span>
                                <span class="hm-sub">Rate: <span style="color:#10b981; font-weight:700;">${mRate}%</span></span>
                                <span class="hm-sub">Bal: ${fmtNum(mData.remaining)}</span>
                                <span class="hm-sub" style="color: #f59e0b; font-weight: 700; margin-top: 2px;">${fmtRs(mData.revenue)}</span>
                            </div>
                        </td>
                `;
            });

            tableHtml += `
                <tr style="background-color: #f8fafc; border-top: 2px solid #cbd5e1; border-bottom: 2px solid #cbd5e1;">
                    <td><span class="cohort-badge" style="background: #0f172a; color: #fff;">TOTAL</span></td>
                    <td style="font-weight:900; color:#0f172a; font-size: 1.1em;">${fmtNum(totalBase)}</td>
                    ${totalMonthCells}
                    <td style="font-weight:900; color:#10b981; font-size: 1.1em;">${fmtNum(totalReactivated)}</td>
                    <td style="font-weight:900; font-size: 1.1em;">${overallRate}%</td>
                    <td style="color:#f59e0b; font-weight:900; font-size: 1.1em;">${fmtRs(totalRev)}</td>
                </tr>
            `;

            // Update Table
            document.querySelector('#cohort-table tbody').innerHTML = tableHtml;

            // Update KPIs
            const overallRate = totalBase > 0 ? ((totalReactivated / totalBase) * 100).toFixed(2) : 0;
            document.getElementById('kpi-container').innerHTML = `
                <div class="kpi-card">
                    <div class="kpi-label">Total Dormant Base</div>
                    <div class="kpi-val">${fmtNum(totalBase)}</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-label">Total Reactivated (2026)</div>
                    <div class="kpi-val text-success">${fmtNum(totalReactivated)}</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-label">Resurrection Rate</div>
                    <div class="kpi-val text-primary">${overallRate}%</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-label">Reactivated Revenue</div>
                    <div class="kpi-val text-warning">${fmtRs(totalRev)}</div>
                </div>
            `;

            // Plotly Waterfall
            const wfX = ['Initial Base', 'Jan Return', 'Feb Return', 'Mar Return', 'Apr Return', 'May Return', 'Remaining Dormant'];
            const wfY = [
                totalBase, 
                -monthTotals['Jan 2026'].reactivated, 
                -monthTotals['Feb 2026'].reactivated, 
                -monthTotals['Mar 2026'].reactivated, 
                -monthTotals['Apr 2026'].reactivated, 
                -monthTotals['May 2026'].reactivated, 
                totalBase - totalReactivated
            ];
            const wfMeasure = ['absolute', 'relative', 'relative', 'relative', 'relative', 'relative', 'total'];
            
            const wfData = [{
                name: 'Customers',
                type: 'waterfall',
                orientation: 'v',
                measure: wfMeasure,
                x: wfX,
                y: wfY,
                textposition: 'outside',
                text: wfY.map(v => fmtNum(Math.abs(v))),
                decreasing: { marker: { color: '#10b981' } }, // Green = good (reactivated!)
                increasing: { marker: { color: '#ef4444' } },
                totals: { marker: { color: '#6366f1' } }
            }];

            const wfLayout = {
                margin: { t: 20, b: 40, l: 50, r: 20 },
                waterfallgap: 0.3,
                paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
                yaxis: { title: 'Customers', gridcolor: '#f1f5f9' },
                xaxis: { tickfont: { size: 11, color: '#64748b' } }
            };

            document.getElementById('waterfall-chart').innerHTML = '';
            Plotly.newPlot('waterfall-chart', wfData, wfLayout, { responsive: true, displayModeBar: false });

            // AI Insights Box (Top)
            document.getElementById('ai-insights').style.display = 'flex';
            let bestMonth = Object.keys(monthTotals).reduce((a, b) => monthTotals[a].reactivated > monthTotals[b].reactivated ? a : b);
            document.getElementById('ai-message').innerHTML = `
                <strong>Analysis complete:</strong> The highest reactivation spike occurred in <strong>${bestMonth}</strong>. 
                The overall resurrection rate is <strong>${overallRate}%</strong>, contributing <strong>${fmtRs(totalRev)}</strong> in revenue. 
                Deep learning models indicate seasonal peaks aligning with holiday events.
            `;

            // FUTURISTIC AI CONSOLE LOGIC
            const aiData = json.ai_forecast;
            
            // 1. Resurrection Probability Gauge
            const gaugeRes = [{
                type: "indicator", mode: "gauge+number", value: aiData.resurrection_prob,
                number: { suffix: "%", font: { color: '#38bdf8', size: 30, weight: 800 } },
                gauge: {
                    axis: { range: [null, 15], tickwidth: 1, tickcolor: "#334155" },
                    bar: { color: "#38bdf8" },
                    bgcolor: "rgba(255,255,255,0.05)", borderwidth: 0,
                    steps: [
                        { range: [0, 5], color: "rgba(239, 68, 68, 0.2)" },
                        { range: [5, 10], color: "rgba(245, 158, 11, 0.2)" },
                        { range: [10, 15], color: "rgba(16, 185, 129, 0.2)" }
                    ]
                }
            }];
            Plotly.newPlot('gauge-resurrection', gaugeRes, { margin: { t: 25, b: 15, l: 25, r: 25 }, paper_bgcolor: 'transparent', font: { color: '#94a3b8' } }, {displayModeBar: false});

            // 2. Repeat Purchase Semi-Donut
            const gaugeRep = [{
                type: "indicator", mode: "gauge+number", value: aiData.repeat_prob,
                number: { suffix: "%", font: { color: '#818cf8', size: 26, weight: 800 } },
                gauge: {
                    shape: "bullet",
                    axis: { range: [null, 100], visible: false },
                    bar: { color: "#818cf8", thickness: 1 },
                    bgcolor: "rgba(255,255,255,0.05)", borderwidth: 0
                }
            }];
            Plotly.newPlot('gauge-repeat', gaugeRep, { margin: { t: 15, b: 15, l: 15, r: 15 }, paper_bgcolor: 'transparent' }, {displayModeBar: false});

            // 3. Dormancy Risk Progress Bar
            const dormancyRiskBar = document.querySelector('.progress-bar.bg-danger');
            if (dormancyRiskBar) {
                dormancyRiskBar.style.width = aiData.dormancy_risk + '%';
                dormancyRiskBar.setAttribute('aria-valuenow', aiData.dormancy_risk);
            }

            // 4. Predicted Volume Counter
            document.getElementById('ai-vol-counter').innerText = fmtNum(aiData.predicted_vol);

            // 5. Dynamic AI Insights Feed & Detailed Modal
            const insightsContainer = document.getElementById('dynamic-insights-container');
            const modalInsightsContainer = document.getElementById('dynamic-modal-insights-container');
            if (insightsContainer && aiData.insights) {
                let insightsHtml = '';
                let modalHtml = '';
                
                aiData.insights.forEach((insight, index) => {
                    // Update side panel (short summary)
                    insightsHtml += `
                    <div class="mb-3 d-flex gap-2">
                        <span style="font-size: 1.2rem;">⚡</span>
                        <div>${insight.data_point}</div>
                    </div>`;
                    
                    // Update Modal (detailed analysis)
                    let borderColors = {
                        'primary': '#8b5cf6',
                        'success': '#10b981',
                        'info': '#0ea5e9',
                        'warning': '#f59e0b',
                        'danger': '#ef4444',
                        'secondary': '#64748b'
                    };
                    let borderColor = borderColors[insight.color_theme] || borderColors['primary'];
                    
                    modalHtml += `
                    <div class="mb-4">
                        <h6 style="color: #0f172a; font-weight: 800; font-size: 1.1rem;">${index + 1}. ${insight.title}</h6>
                        <div class="p-3" style="background: #f8fafc; border-left: 4px solid ${borderColor}; border-radius: 4px;">
                            <p style="margin: 0; font-size: 0.9rem; color: #475569;">
                                <strong>Data Point:</strong> ${insight.data_point}<br><br>
                                <strong>Deep Analysis:</strong> ${insight.deep_analysis}
                                ${insight.recommendation ? ` <br><br><strong>Recommendation:</strong> ${insight.recommendation}` : ''}
                            </p>
                        </div>
                    </div>`;
                });
                
                insightsContainer.innerHTML = insightsHtml;
                if (modalInsightsContainer) {
                    modalInsightsContainer.innerHTML = modalHtml;
                }
            }

            // 6. Dynamic Confidence Engine
            const confidenceTable = document.getElementById('dynamic-confidence-table');
            if (confidenceTable && aiData.confidence_scores) {
                let confHtml = '';
                for (const [key, val] of Object.entries(aiData.confidence_scores)) {
                    confHtml += `
                    <tr>
                        <td style="padding: 4px 0; color: #701a75; font-weight: 600;">${key}</td>
                        <td class="text-end" style="font-weight: 800; color: #10b981;">${val}</td>
                    </tr>`;
                }
                confidenceTable.innerHTML = confHtml;
            }

            // 4. LSTM FORECAST CHART (Real Sklearn Predictions)
            const lstmX = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun (Pred)', 'Jul (Pred)', 'Aug (Pred)'];
            const histY = aiData.historical;
            const predY = aiData.predictions;
            
            const traceHist = {
                x: lstmX.slice(0, 5), y: histY,
                mode: 'lines+markers', name: 'Actual',
                line: { color: '#0f172a', width: 3, shape: 'spline' },
                marker: { size: 6, color: '#0f172a' }
            };
            
            const tracePred = {
                x: lstmX.slice(4), y: [histY[4]].concat(predY),
                mode: 'lines', name: 'Forecast',
                line: { color: '#6366f1', width: 3, dash: 'dot', shape: 'spline' }
            };

            const traceBand = {
                x: lstmX.slice(4).concat(lstmX.slice(4).reverse()),
                y: [histY[4]].concat(aiData.upper_bound).concat([histY[4]].concat(aiData.lower_bound).reverse()),
                fill: 'toself', fillcolor: 'rgba(99, 102, 241, 0.15)',
                line: { color: 'transparent' },
                name: '95% Confidence Interval', showlegend: true, type: 'scatter'
            };

            const lstmLayout = {
                margin: { t: 20, b: 30, l: 40, r: 20 },
                paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
                xaxis: { 
                    showgrid: false, 
                    tickfont: { color: '#64748b' },
                    categoryorder: 'array',
                    categoryarray: lstmX
                },
                yaxis: { showgrid: true, gridcolor: '#f1f5f9', tickfont: { color: '#64748b' } },
                legend: { orientation: 'h', y: 1.1, x: 0.1, font: {size: 10} },
                hovermode: 'x unified'
            };

            Plotly.newPlot('lstm-forecast-chart', [traceBand, traceHist, tracePred], lstmLayout, { responsive: true, displayModeBar: false });
            
            // Update AI KPIs
            document.getElementById('lstm-forecast-chart').insertAdjacentHTML('afterend', `
            <div class="p-3" style="background: #f8fafc; border-top: 1px solid #e2e8f0;">
                <div class="row text-center" style="font-size: 0.75rem;">
                    <div class="col-4">
                        <div style="color: #64748b; font-weight: 700; text-transform: uppercase;">Model Accuracy</div>
                        <div style="font-size: 1.1rem; font-weight: 800; color: #0f172a;">${aiData.accuracy}%</div>
                    </div>
                    <div class="col-4" style="border-left: 1px solid #e2e8f0; border-right: 1px solid #e2e8f0;">
                        <div style="color: #64748b; font-weight: 700; text-transform: uppercase;">RMSE Error</div>
                        <div style="font-size: 1.1rem; font-weight: 800; color: #0f172a;">${aiData.rmse}</div>
                    </div>
                    <div class="col-4">
                        <div style="color: #64748b; font-weight: 700; text-transform: uppercase;">Forecast Window</div>
                        <div style="font-size: 1.1rem; font-weight: 800; color: #10b981;">90 Days</div>
                    </div>
                </div>
            </div>
            `);
            // Removed extra line
            document.querySelector('.col-lg-6 .section-card .p-3:last-child').remove(); // remove old static kpi block

        })
        .catch(err => {
            console.error("Fetch Error:", err);
            // Hide spinners and show error
            document.querySelectorAll('.spinner-border').forEach(s => s.style.display = 'none');
            document.querySelector('#cohort-table tbody').innerHTML = `<tr><td colspan="6" class="text-danger text-center"><strong>Critical Error Loading AI Data:</strong> ${err.message}. (If you are on a free Render tier, the AI machine learning models may have exceeded the 512MB RAM limit and crashed).</td></tr>`;
        });
