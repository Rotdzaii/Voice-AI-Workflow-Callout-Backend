// Node.js Express server: API nhận số điện thoại từ frontend và chuyển về backend để xử lý gọi

const express = require('express');
const axios = require('axios');
const cors = require('cors');
const app = express();
const PORT = process.env.PORT || 4001;

app.use(cors());
app.use(express.json());

// Endpoint nhận số điện thoại từ frontend
app.post('/api/start_call', async (req, res) => {
    const { phone } = req.body;
    if (!phone) {
        return res.status(400).json({ error: 'Missing phone number' });
    }
    try {
        // Gửi số điện thoại về backend FastAPI để xử lý gọi
        const backendUrl = process.env.BACKEND_URL || 'http://localhost:8000/call/start';
        const response = await axios.post(backendUrl, { phone });
        return res.json(response.data);
    } catch (err) {
        return res.status(500).json({ error: err.message });
    }
});

app.listen(PORT, () => {
    console.log(`Node start_call API listening on port ${PORT}`);
});
