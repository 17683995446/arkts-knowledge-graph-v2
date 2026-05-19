/**
 * ArkTS Knowledge Graph - Frontend Application
 */

// Global state
const state = {
    socket: null,
    graphData: { entities: [], relationships: [], files: [], dependencies: [] },
    selectedEntity: null,
    currentTab: 'dashboard',
    simulation: null
};

// Initialize application
document.addEventListener('DOMContentLoaded', () => {
    initSocket();
    initTabs();
    initMermaid();
    initGraphControls();
    loadStats();
    loadEvents();
    
    // Auto-refresh stats every 5 seconds
    setInterval(loadStats, 5000);
});

// Socket.IO connection
function initSocket() {
    state.socket = io();
    
    state.socket.on('connect', () => {
        updateConnectionStatus(true);
        showToast('已连接到服务器', 'success');
    });
    
    state.socket.on('disconnect', () => {
        updateConnectionStatus(false);
        showToast('与服务器断开连接', 'info');
    });
    
    state.socket.on('file_updated', (data) => {
        handleFileUpdate(data);
    });
}

function updateConnectionStatus(connected) {
    const statusEl = document.getElementById('connection-status');
    if (connected) {
        statusEl.textContent = '● 已连接';
        statusEl.classList.remove('disconnected');
        statusEl.classList.add('connected');
    } else {
        statusEl.textContent = '● 未连接';
        statusEl.classList.remove('connected');
        statusEl.classList.add('disconnected');
    }
}

function handleFileUpdate(data) {
    showToast(`文件已更新: ${getFileName(data.file_path)}`, 'success');
    document.getElementById('last-update').textContent = 
        `最后更新: ${new Date().toLocaleTimeString()}`;
    
    // Refresh data
    loadStats();
    if (state.currentTab === 'knowledge-graph') {
        loadGraphData();
    }
    if (state.currentTab === 'events') {
        loadEvents();
    }
}

// Tab navigation
function initTabs() {
    const tabs = document.querySelectorAll('.nav-tab');
    const contents = document.querySelectorAll('.tab-content');
    
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const tabId = tab.dataset.tab;
            
            // Update active states
            tabs.forEach(t => t.classList.remove('active'));
            contents.forEach(c => c.classList.remove('active'));
            
            tab.classList.add('active');
            document.getElementById(tabId).classList.add('active');
            
            state.currentTab = tabId;
            
            // Load tab-specific data
            if (tabId === 'knowledge-graph') {
                loadGraphData();
            } else if (tabId === 'events') {
                loadEvents();
            } else if (tabId === 'dashboard') {
                loadStats();
            }
        });
    });
}

// Mermaid initialization
function initMermaid() {
    mermaid.initialize({
        startOnLoad: true,
        theme: 'default',
        flowchart: {
            useMaxWidth: true,
            htmlLabels: true,
            curve: 'basis'
        },
        sequence: {
            useMaxWidth: true,
            diagramMarginX: 50,
            diagramMarginY: 10
        }
    });
}

// Load statistics
async function loadStats() {
    try {
        const response = await fetch('/api/stats');
        const stats = await response.json();
        
        document.getElementById('stat-files').textContent = stats.total_files || 0;
        document.getElementById('stat-entities').textContent = stats.total_entities || 0;
        document.getElementById('stat-relations').textContent = stats.total_relationships || 0;
        document.getElementById('stat-imports').textContent = stats.total_imports || 0;
        
        // Update charts
        renderEntityTypesChart(stats.entities_by_type || {});
        renderRelationTypesChart(stats.relations_by_type || {});
    } catch (error) {
        console.error('Failed to load stats:', error);
    }
}

// Render entity types chart
function renderEntityTypesChart(data) {
    const container = document.getElementById('entity-types-chart');
    if (!container || Object.keys(data).length === 0) {
        container.innerHTML = '<p style="color: #999;">暂无数据</p>';
        return;
    }
    
    const colors = {
        function: '#4caf50',
        class: '#2196f3',
        interface: '#9c27b0',
        enum: '#ff9800',
        type: '#00bcd4',
        variable: '#795548'
    };
    
    const total = Object.values(data).reduce((a, b) => a + b, 0);
    
    let html = '<div style="display: flex; flex-wrap: wrap; gap: 15px;">';
    for (const [type, count] of Object.entries(data)) {
        const percentage = ((count / total) * 100).toFixed(1);
        const color = colors[type] || '#757575';
        html += `
            <div style="display: flex; align-items: center; gap: 8px; padding: 10px 15px; background: #f5f5f5; border-radius: 8px;">
                <span style="width: 12px; height: 12px; background: ${color}; border-radius: 50%;"></span>
                <span style="font-weight: 500;">${type}</span>
                <span style="color: #666;">${count} (${percentage}%)</span>
            </div>
        `;
    }
    html += '</div>';
    
    container.innerHTML = html;
}

// Render relation types chart
function renderRelationTypesChart(data) {
    const container = document.getElementById('relation-types-chart');
    if (!container || Object.keys(data).length === 0) {
        container.innerHTML = '<p style="color: #999;">暂无数据</p>';
        return;
    }
    
    const colors = {
        calls: '#4caf50',
        extends: '#2196f3',
        implements: '#9c27b0',
        imports: '#ff9800',
        references: '#00bcd4',
        uses: '#795548'
    };
    
    const total = Object.values(data).reduce((a, b) => a + b, 0);
    
    let html = '<div style="display: flex; flex-wrap: wrap; gap: 15px;">';
    for (const [type, count] of Object.entries(data)) {
        const percentage = ((count / total) * 100).toFixed(1);
        const color = colors[type] || '#757575';
        html += `
            <div style="display: flex; align-items: center; gap: 8px; padding: 10px 15px; background: #f5f5f5; border-radius: 8px;">
                <span style="width: 12px; height: 12px; background: ${color}; border-radius: 50%;"></span>
                <span style="font-weight: 500;">${type}</span>
                <span style="color: #666;">${count} (${percentage}%)</span>
            </div>
        `;
    }
    html += '</div>';
    
    container.innerHTML = html;
}

// Load graph data
async function loadGraphData() {
    try {
        const response = await fetch('/api/graph');
        state.graphData = await response.json();
        renderKnowledgeGraph();
    } catch (error) {
        console.error('Failed to load graph data:', error);
    }
}

// Render D3 knowledge graph
function renderKnowledgeGraph() {
    const container = document.getElementById('graph-container');
    const svg = d3.select('#knowledge-graph-svg');
    svg.selectAll('*').remove();
    
    const width = container.clientWidth;
    const height = container.clientHeight;
    
    svg.attr('width', width).attr('height', height);
    
    const { entities, relationships } = state.graphData;
    
    if (entities.length === 0) {
        svg.append('text')
            .attr('x', width / 2)
            .attr('y', height / 2)
            .attr('text-anchor', 'middle')
            .style('fill', '#999')
            .text('暂无数据，请添加ArkTS文件');
        return;
    }
    
    // Prepare nodes and links
    const nodes = entities.map(e => ({
        id: e.id,
        name: e.name,
        type: e.type,
        file_path: e.file_path,
        signature: e.signature
    }));
    
    const nodeIds = new Set(nodes.map(n => n.id));
    const links = relationships
        .filter(r => r.source_entity_id && r.target_entity_id)
        .filter(r => nodeIds.has(r.source_entity_id) && nodeIds.has(r.target_entity_id))
        .map(r => ({
            source: r.source_entity_id,
            target: r.target_entity_id,
            type: r.relation_type
        }));
    
    // Create force simulation
    state.simulation = d3.forceSimulation(nodes)
        .force('link', d3.forceLink(links).id(d => d.id).distance(100))
        .force('charge', d3.forceManyBody().strength(-300))
        .force('center', d3.forceCenter(width / 2, height / 2))
        .force('collision', d3.forceCollide().radius(30));
    
    // Create container for zoom
    const g = svg.append('g');
    
    // Add zoom behavior
    const zoom = d3.zoom()
        .scaleExtent([0.1, 4])
        .on('zoom', (event) => {
            g.attr('transform', event.transform);
        });
    
    svg.call(zoom);
    
    // Draw links
    const link = g.append('g')
        .selectAll('line')
        .data(links)
        .enter().append('line')
        .attr('class', d => `link ${d.type}`)
        .attr('stroke-width', 1.5);
    
    // Draw nodes
    const node = g.append('g')
        .selectAll('.node')
        .data(nodes)
        .enter().append('g')
        .attr('class', d => `node ${d.type}`)
        .call(d3.drag()
            .on('start', dragstarted)
            .on('drag', dragged)
            .on('end', dragended));
    
    // Add circles to nodes
    node.append('circle')
        .attr('r', 8)
        .attr('fill', d => getNodeColor(d.type));
    
    // Add labels to nodes
    node.append('text')
        .attr('dx', 12)
        .attr('dy', 4)
        .text(d => d.name)
        .style('fill', '#333')
        .style('font-size', '12px');
    
    // Node click handler
    node.on('click', (event, d) => {
        event.stopPropagation();
        showEntityDetails(d);
    });
    
    // Update positions on tick
    state.simulation.on('tick', () => {
        link
            .attr('x1', d => d.source.x)
            .attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x)
            .attr('y2', d => d.target.y);
        
        node.attr('transform', d => `translate(${d.x},${d.y})`);
    });
    
    // Click background to close details
    svg.on('click', () => {
        hideEntityDetails();
    });
}

function getNodeColor(type) {
    const colors = {
        function: '#4caf50',
        class: '#2196f3',
        interface: '#9c27b0',
        enum: '#ff9800',
        type: '#00bcd4',
        variable: '#795548'
    };
    return colors[type] || '#757575';
}

function dragstarted(event, d) {
    if (!event.active) state.simulation.alphaTarget(0.3).restart();
    d.fx = d.x;
    d.fy = d.y;
}

function dragged(event, d) {
    d.fx = event.x;
    d.fy = event.y;
}

function dragended(event, d) {
    if (!event.active) state.simulation.alphaTarget(0);
    d.fx = null;
    d.fy = null;
}

// Graph controls
function initGraphControls() {
    // Search button
    document.getElementById('search-btn').addEventListener('click', searchEntities);
    
    // Search on Enter key
    document.getElementById('search-input').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') searchEntities();
    });
    
    // Reset button
    document.getElementById('reset-graph-btn').addEventListener('click', () => {
        document.getElementById('search-input').value = '';
        renderKnowledgeGraph();
    });
    
    // Filter change
    document.getElementById('entity-filter').addEventListener('change', filterEntities);
    
    // Refresh events button
    document.getElementById('refresh-events').addEventListener('click', loadEvents);
    
    // Clear events button
    document.getElementById('clear-events').addEventListener('click', () => {
        document.getElementById('events-tbody').innerHTML = '';
    });
}

async function searchEntities() {
    const query = document.getElementById('search-input').value.trim();
    if (!query) return;
    
    try {
        const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
        const results = await response.json();
        
        if (results.length === 0) {
            showToast('未找到匹配的实体', 'info');
            return;
        }
        
        // Highlight matching nodes
        const matchingIds = new Set(results.map(r => r.id));
        d3.selectAll('.node')
            .style('opacity', d => matchingIds.has(d.id) ? 1 : 0.2);
        
        d3.selectAll('.link')
            .style('opacity', 0.1);
        
        showToast(`找到 ${results.length} 个匹配项`, 'success');
    } catch (error) {
        console.error('Search failed:', error);
        showToast('搜索失败', 'info');
    }
}

function filterEntities() {
    const select = document.getElementById('entity-filter');
    const selectedTypes = Array.from(select.selectedOptions).map(o => o.value);
    
    d3.selectAll('.node')
        .style('display', d => selectedTypes.includes(d.type) ? 'block' : 'none');
}

// Entity details
async function showEntityDetails(entity) {
    state.selectedEntity = entity;
    const panel = document.getElementById('entity-details');
    const content = document.getElementById('entity-details-content');
    
    try {
        const response = await fetch(`/api/entity/${entity.id}`);
        const details = await response.json();
        
        let html = `
            <div class="detail-item">
                <strong>名称:</strong> ${details.name}
            </div>
            <div class="detail-item">
                <strong>类型:</strong> <span class="badge ${details.type}">${details.type}</span>
            </div>
            <div class="detail-item">
                <strong>文件:</strong> ${getFileName(details.file_path)}
            </div>
        `;
        
        if (details.signature) {
            html += `
                <div class="detail-item">
                    <strong>签名:</strong>
                    <pre style="background: #f5f5f5; padding: 10px; border-radius: 4px; overflow-x: auto; margin-top: 5px;">${details.signature}</pre>
                </div>
            `;
        }
        
        if (details.docstring) {
            html += `
                <div class="detail-item">
                    <strong>文档:</strong>
                    <p style="margin-top: 5px; color: #666;">${details.docstring}</p>
                </div>
            `;
        }
        
        if (details.outgoing_relations && details.outgoing_relations.length > 0) {
            html += `
                <div class="detail-item">
                    <strong>调用/引用:</strong>
                    <ul style="margin-top: 5px; padding-left: 20px;">
                        ${details.outgoing_relations.map(r => 
                            `<li>${r.relation_type} → ${r.target_name || 'unknown'}</li>`
                        ).join('')}
                    </ul>
                </div>
            `;
        }
        
        if (details.incoming_relations && details.incoming_relations.length > 0) {
            html += `
                <div class="detail-item">
                    <strong>被调用/引用:</strong>
                    <ul style="margin-top: 5px; padding-left: 20px;">
                        ${details.incoming_relations.map(r => 
                            `<li>${r.source_name || 'unknown'} → ${r.relation_type}</li>`
                        ).join('')}
                    </ul>
                </div>
            `;
        }
        
        content.innerHTML = html;
        panel.classList.remove('hidden');
    } catch (error) {
        console.error('Failed to load entity details:', error);
    }
}

function hideEntityDetails() {
    document.getElementById('entity-details').classList.add('hidden');
    state.selectedEntity = null;
}

// Load events
async function loadEvents() {
    try {
        const response = await fetch('/api/events');
        const events = await response.json();
        
        const tbody = document.getElementById('events-tbody');
        
        if (events.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: #999;">暂无事件</td></tr>';
            return;
        }
        
        tbody.innerHTML = events.map(e => `
            <tr>
                <td>${formatDate(e.created_at)}</td>
                <td data-type="${e.event_type}">${e.event_type}</td>
                <td>${e.file_path ? getFileName(e.file_path) : '-'}</td>
                <td>${e.details || '-'}</td>
                <td>${e.duration_ms ? e.duration_ms + 'ms' : '-'}</td>
            </tr>
        `).join('');
    } catch (error) {
        console.error('Failed to load events:', error);
    }
}

// Utility functions
function getFileName(path) {
    if (!path) return '-';
    return path.split('/').pop();
}

function formatDate(dateStr) {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleString('zh-CN');
}

function showToast(message, type = 'info') {
    // Create toast container if not exists
    let container = document.querySelector('.toast-container');
    if (!container) {
        container = document.createElement('div');
        container.className = 'toast-container';
        document.body.appendChild(container);
    }
    
    // Create toast
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <span>${type === 'success' ? '✓' : 'ℹ'}</span>
        <span>${message}</span>
    `;
    
    container.appendChild(toast);
    
    // Remove after 3 seconds
    setTimeout(() => {
        toast.style.animation = 'slideIn 0.3s ease reverse';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}
