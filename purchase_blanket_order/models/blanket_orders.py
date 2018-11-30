# Copyright (C) 2018 Eficent Business and IT Consulting Services S.L.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import fields, models, api, _
from odoo.exceptions import UserError
from odoo.tools import float_is_zero

import odoo.addons.decimal_precision as dp


class BlanketOrder(models.Model):
    _name = 'purchase.blanket.order'
    _inherit = ['mail.thread']
    _description = 'Blanket Order'

    @api.model
    def _default_currency(self):
        return self.env.user.company_id.currency_id

    @api.model
    def _default_company(self):
        return self.env.user.company_id

    name = fields.Char(
        default='Draft',
        readonly=True
    )
    partner_id = fields.Many2one(
        'res.partner', string='Vendor', readonly=True,
        states={'draft': [('readonly', False)]})
    lines_ids = fields.One2many(
        'purchase.blanket.order.line', 'order_id', string='Order lines',
        copy=True)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    payment_term_id = fields.Many2one(
        'account.payment.term', string='Payment Terms', readonly=True,
        states={'draft': [('readonly', False)]})
    confirmed = fields.Boolean()
    state = fields.Selection(selection=[
        ('draft', 'Draft'),
        ('open', 'Open'),
        ('expired', 'Expired'),
    ], compute='_compute_state', store=True, copy=False)
    validity_date = fields.Date(
        readonly=True,
        states={'draft': [('readonly', False)]})
    date_order = fields.Datetime(
        readonly=True,
        required=True,
        string='Ordering Date',
        default=fields.Datetime.now,
        states={'draft': [('readonly', False)]})
    date_deliver = fields.Datetime(
        readonly=True,
        string='Delivery Date',
        states={'draft': [('readonly', False)]})
    note = fields.Text(
        readonly=True,
        states={'draft': [('readonly', False)]})
    user_id = fields.Many2one(
        'res.users', string='Responsible', readonly=True,
        states={'draft': [('readonly', False)]})
    company_id = fields.Many2one(
        'res.company', string='Company', default=_default_company,
        readonly=True,
        states={'draft': [('readonly', False)]})
    purchase_count = fields.Integer(compute='_compute_purchase_count')

    # Fields use to filter in tree view
    original_qty = fields.Float(
        string='Original quantity', compute='_compute_original_qty',
        search='_search_original_qty', default=0.0)
    ordered_qty = fields.Float(
        string='Ordered quantity', compute='_compute_ordered_qty',
        search='_search_ordered_qty', default=0.0)
    invoiced_qty = fields.Float(
        string='Invoiced quantity', compute='_compute_invoiced_qty',
        search='_search_invoiced_qty', default=0.0)
    remaining_qty = fields.Float(
        string='Remaining quantity', compute='_compute_remaining_qty',
        search='_search_remaining_qty', default=0.0)
    received_qty = fields.Float(
        string='Delivered quantity', compute='_compute_received_qty',
        search='_search_received_qty', default=0.0)

    @api.multi
    def _get_purchase_orders(self):
        return self.mapped('lines_ids.purchase_order_lines_ids.order_id')

    @api.multi
    def _compute_purchase_count(self):
        for blanket_order in self:
            blanket_order.purchase_count = \
                len(blanket_order._get_purchase_orders())

    @api.multi
    @api.depends(
        'lines_ids.remaining_qty',
        'validity_date',
        'confirmed',
    )
    def _compute_state(self):
        today = fields.Date.today()
        precision = self.env['decimal.precision'].precision_get(
            'Product Unit of Measure')
        for order in self:
            if not order.confirmed:
                order.state = 'draft'
            elif order.validity_date <= today:
                order.state = 'expired'
            elif float_is_zero(sum(order.lines_ids.mapped('remaining_qty')),
                               precision_digits=precision):
                order.state = 'expired'
            else:
                order.state = 'open'

    def _compute_original_qty(self):
        for bo in self:
            bo.original_qty = sum(bo.mapped('order_id.original_qty'))

    def _compute_ordered_qty(self):
        for bo in self:
            bo.ordered_qty = sum(bo.mapped('order_id.ordered_qty'))

    def _compute_invoiced_qty(self):
        for bo in self:
            bo.invoiced_qty = sum(bo.mapped('order_id.invoiced_qty'))

    def _compute_received_qty(self):
        for bo in self:
            bo.received_qty = sum(bo.mapped('order_id.received_qty'))

    def _compute_remaining_qty(self):
        for bo in self:
            bo.remaining_qty = sum(bo.mapped('order_id.remaining_qty'))

    @api.multi
    @api.onchange('partner_id')
    def onchange_partner_id(self):
        """
        Update the following fields when the partner is changed:
        - Payment term
        """
        if not self.partner_id:
            self.payment_term_id = False
            return

        self.payment_term_id = \
            (self.partner_id.property_supplier_payment_term_id and
             self.partner_id.property_supplier_payment_term_id.id or
             False)

        if self.partner_id.user_id:
            self.user_id = self.partner_id.user_id.id

    @api.multi
    def copy_data(self, default=None):
        if default is None:
            default = {}
        default.update(self.default_get(['name', 'confirmed']))
        return super(BlanketOrder, self).copy_data(default)

    @api.multi
    def _validate(self):
        try:
            today = fields.Date.today()
            for order in self:
                assert order.validity_date, _("Validity date is mandatory")
                assert order.validity_date > today, \
                    _("Validity date must be in the future")
                assert order.partner_id, _("Partner is mandatory")
                assert len(order.lines_ids) > 0, _("Must have some lines")
                order.lines_ids._validate()
        except AssertionError as e:
            raise UserError(e)

    @api.multi
    def action_confirm(self):
        self._validate()
        for order in self:
            sequence_obj = self.env['ir.sequence']
            if order.company_id:
                sequence_obj = sequence_obj.with_context(
                    force_company=order.company_id.id)
            name = sequence_obj.next_by_code('purchase.blanket.order')
            order.write({'confirmed': True, 'name': name})
        return True

    @api.multi
    def action_view_purchase_orders(self):
        purchase_orders = self._get_purchase_orders()
        action = self.env.ref('purchase.purchase_rfq').read()[0]
        if len(purchase_orders) > 0:
            action['domain'] = [('id', 'in', purchase_orders.ids)]
            action['context'] = [('id', 'in', purchase_orders.ids)]
        else:
            action = {'type': 'ir.actions.act_window_close'}
        return action

    @api.model
    def expire_orders(self):
        today = fields.Date.today()
        expired_orders = self.search([
            ('state', '=', 'open'),
            ('validity_date', '<=', today),
        ])
        expired_orders.modified(['validity_date'])
        expired_orders.recompute()

    @api.model
    def _search_original_qty(self, operator, value):
        bo_line_obj = self.env['purchase.blanket.order.line']
        res = []
        bo_lines = bo_line_obj.search(
            [('original_qty', operator, value)])
        order_ids = bo_lines.mapped('order_id')
        res.append(('id', 'in', order_ids.ids))
        return res

    @api.model
    def _search_ordered_qty(self, operator, value):
        bo_line_obj = self.env['purchase.blanket.order.line']
        res = []
        bo_lines = bo_line_obj.search(
            [('ordered_qty', operator, value)])
        order_ids = bo_lines.mapped('order_id')
        res.append(('id', 'in', order_ids.ids))
        return res

    @api.model
    def _search_invoiced_qty(self, operator, value):
        bo_line_obj = self.env['purchase.blanket.order.line']
        res = []
        bo_lines = bo_line_obj.search(
            [('invoiced_qty', operator, value)])
        order_ids = bo_lines.mapped('order_id')
        res.append(('id', 'in', order_ids.ids))
        return res

    @api.model
    def _search_received_qty(self, operator, value):
        bo_line_obj = self.env['purchase.blanket.order.line']
        res = []
        bo_lines = bo_line_obj.search(
            [('received_qty', operator, value)])
        order_ids = bo_lines.mapped('order_id')
        res.append(('id', 'in', order_ids.ids))
        return res

    @api.model
    def _search_remaining_qty(self, operator, value):
        bo_line_obj = self.env['purchase.blanket.order.line']
        res = []
        bo_lines = bo_line_obj.search(
            [('remaining_qty', operator, value)])
        order_ids = bo_lines.mapped('order_id')
        res.append(('id', 'in', order_ids.ids))
        return res


class BlanketOrderLine(models.Model):
    _name = 'purchase.blanket.order.line'
    _description = 'Blanket Order Line'

    sequence = fields.Integer()
    order_id = fields.Many2one(
        'purchase.blanket.order', required=True, ondelete='cascade')
    product_id = fields.Many2one(
        'product.product', string='Product', required=True)
    product_uom = fields.Many2one(
        'product.uom', string='Unit of Measure', required=True)
    price_unit = fields.Float(string='Price', required=True)
    original_qty = fields.Float(
        string='Original quantity', required=True, default=1,
        digits=dp.get_precision('Product Unit of Measure'))
    ordered_qty = fields.Float(
        string='Ordered quantity', compute='_compute_quantities',
        store=True)
    invoiced_qty = fields.Float(
        string='Invoiced quantity', compute='_compute_quantities',
        store=True)
    remaining_qty = fields.Float(
        string='Remaining quantity', compute='_compute_quantities',
        store=True)
    received_qty = fields.Float(
        string='Received quantity', compute='_compute_quantities',
        store=True)
    purchase_order_lines_ids = fields.One2many(
        'purchase.order.line', 'blanket_line_id', string='Sale order lines')
    company_id = fields.Many2one(
        'res.company', related='order_id.company_id', store=True,
        readonly=True)

    @api.multi
    def _get_display_price(self, product):

        seller = product._select_seller(
            partner_id=self.order_id.partner_id,
            quantity=self.original_qty,
            date=self.order_id.date_order and self.order_id.date_order[:10],
            uom_id=self.product_uom)

        if not seller:
            return

        price_unit = self.env['account.tax']._fix_tax_included_price_company(
            seller.price, product.supplier_taxes_id,
            self.purchase_order_lines_ids.taxes_id,
            self.company_id) if seller else 0.0
        if price_unit and seller and self.order_id.currency_id and \
                seller.currency_id != self.order_id.currency_id:
            price_unit = seller.currency_id.compute(price_unit,
                                                    self.order_id.currency_id)

        if seller and self.product_uom and seller.product_uom != \
                self.product_uom:
            price_unit = seller.product_uom._compute_price(price_unit,
                                                           self.product_uom)

        return price_unit

    @api.multi
    @api.onchange('product_id', 'original_qty')
    def onchange_product(self):
        precision = self.env['decimal.precision'].precision_get(
            'Product Unit of Measure')
        if self.product_id:
            self.product_uom = self.product_id.uom_id.id
            if self.order_id.partner_id and \
                    float_is_zero(self.price_unit, precision_digits=precision):
                self.price_unit = self._get_display_price(self.product_id)

    @api.multi
    @api.depends(
        'purchase_order_lines_ids.order_id.state',
        'purchase_order_lines_ids.blanket_line_id',
        'purchase_order_lines_ids.product_qty',
        'purchase_order_lines_ids.qty_received',
        'purchase_order_lines_ids.qty_invoiced',
        'original_qty',
    )
    def _compute_quantities(self):
        for line in self:
            purchase_lines = line.purchase_order_lines_ids
            line.ordered_qty = sum(l.product_qty for l in purchase_lines if
                                   l.order_id.state != 'cancel')
            line.invoiced_qty = sum(l.qty_invoiced for l in purchase_lines if
                                    l.order_id.state != 'cancel')
            line.received_qty = sum(l.qty_received for l in purchase_lines if
                                    l.order_id.state != 'cancel')
            line.remaining_qty = line.original_qty - line.ordered_qty

    @api.multi
    def _validate(self):
        try:
            for line in self:
                assert line.price_unit > 0.0, \
                    _("Price must be greater than zero")
                assert line.original_qty > 0.0, \
                    _("Quantity must be greater than zero")
        except AssertionError as e:
            raise UserError(e)
