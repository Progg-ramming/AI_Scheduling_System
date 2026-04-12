const pool = require('./db');

async function check() {
    try {
        const res = await pool.query(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'tasks'"
        );
        console.log('Columns in tasks table:', res.rows);

        const tasks = await pool.query(
            "SELECT id, title, priority, status FROM tasks LIMIT 5"
        );
        console.log('Sample tasks:', tasks.rows);
    } catch (err) {
        console.error('DB Check error:', err);
    } finally {
        await pool.end();
    }
}

check();